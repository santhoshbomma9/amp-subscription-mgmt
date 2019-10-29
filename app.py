import uuid
import requests
from flask import Flask, render_template, session, request, redirect, url_for, jsonify
from flask_session import Session  # https://pythonhosted.org/Flask-Session
import msal
import app_config

app = Flask(__name__)
app.config.from_object(app_config)
Session(app)


@app.route("/")
def login():
    if not session.get("user"):
        session["state"] = str(uuid.uuid4())
        auth_url = _build_msal_app().get_authorization_request_url(
                    app_config.SCOPE,  # Technically we can use empty list [] to just sign in,
                               # here we choose to also collect end user consent upfront
                    state=session["state"],
                    redirect_uri=url_for("authorized", _external=True))

        return redirect(auth_url, code=302)
    else:
        subscriptions = get_subscriptions()
        return render_template('index.html', user=session["user"], subscriptions= subscriptions, version=msal.__version__)

@app.route(app_config.REDIRECT_PATH)  # Its absolute URL must match your app's redirect_uri set in AAD
def authorized():
    if request.args.get('state') == session.get("state"):
        cache = _load_cache()
        result = _build_msal_app(cache=cache).acquire_token_by_authorization_code(
            request.args['code'],
            scopes=app_config.SCOPE,  # Misspelled scope would cause an HTTP 400 error here
            redirect_uri=url_for("authorized", _external=True))
        if "error" in result:
            return "Login failure: %s, %s" % (
                result["error"], result.get("error_description"))
        session["user"] = result.get("id_token_claims")
        _save_cache(cache)
    return redirect(url_for("login"))

@app.route("/edit/<subscriptionid>")
def edit(subscriptionid):
    subscription = get_subscription(subscriptionid)
    plans = get_availableplans(subscriptionid)
    return render_template('managesubscription.html', user=session["user"], subscription = subscription, available_plans= plans)

@app.route("/update", methods=['POST'])
def updatesubscription():
    selected_subscription = request.form['subscription_id']
    selected_plan = request.form['selectedplan']
    update_subscription_response = update_subscriptionplan(selected_subscription, selected_plan)
    app.logger.info(update_subscription_response.status_code)
    if update_subscription_response.status_code == 202:
        return redirect(url_for("login"))
    else:
        return render_template('error.html', user=session["user"], response_statuscode = update_subscription_response.status_code)

@app.route("/operations/<subscriptionid>")
def operations(subscriptionid):
    sub_operations = get_sub_operations(subscriptionid)
    app.logger.info(type(sub_operations))
    return render_template('suboperations.html', user=session["user"], operations = sub_operations)

@app.route("/operation/<subscriptionId>/<operationid>")
def getoperation(subscriptionId, operationid):
    operation = get_operation(subscriptionId, operationid)
    app.logger.info(type(operation))
    return render_template('error.html', user=session["user"], operation = operation)

@app.route("/updateoperation/<operationid>")
def updateoperation(operationid):
    #sub_operations = get_sub_operations(operationid)
    app.logger.info(type(sub_operations))
    #return render_template('suboperations.html', user=session["user"], operations = sub_operations)

# todo change quantity

# todo delete subscription

@app.route("/logout")
def logout():
    session.clear()  # Wipe out user and its token cache from session
    return redirect(  # Also logout from your tenant's web session
        app_config.AUTHORITY + "/" + app_config.TENANT_ID + "/oauth2/v2.0/logout" +
        "?post_logout_redirect_uri=" + url_for("login", _external=True))

def _load_cache():
    cache = msal.SerializableTokenCache()
    if session.get("token_cache"):
        cache.deserialize(session["token_cache"])
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        session["token_cache"] = cache.serialize()

def _build_msal_app(cache=None):
    return msal.ConfidentialClientApplication(
        app_config.CLIENT_ID,
        authority=app_config.AUTHORITY + app_config.TENANT_ID,
        client_credential=app_config.CLIENT_SECRET, 
        token_cache=cache)

def _get_token_from_cache(scope=None):
    cache = _load_cache()  # This web app maintains one cache per session
    cca = _build_msal_app(cache=cache)
    accounts = cca.get_accounts()
    if accounts:  # So all account(s) belong to the current signed-in user
        result = cca.acquire_token_silent(scope, account=accounts[0])
        _save_cache(cache)
        return result


def get_subscriptions():
    subscriptions_data=  call_marketplace_api(app_config.MARKETPLACEAPI_ENDPOINT+ app_config.MARKETPLACEAPI_API_VERSION)
    return subscriptions_data
 
def get_subscription(subscription):
    subscription_data=  call_marketplace_api(  # Use token to call downstream service
        app_config.MARKETPLACEAPI_ENDPOINT +"/"+ subscription + app_config.MARKETPLACEAPI_API_VERSION)
    return subscription_data

def get_availableplans(subscription):
    availableplans = call_marketplace_api(
        request_url=app_config.MARKETPLACEAPI_ENDPOINT +"/"+ subscription + "/listAvailablePlans" + app_config.MARKETPLACEAPI_API_VERSION)
    app.logger.info('%s availableplans', availableplans.items())
    return availableplans

def update_subscriptionplan(subscription, plan_id):
    request_plan_payload = "{\"planId\": \""+ plan_id +"\" }"
    updateresponse = call_marketplace_api(app_config.MARKETPLACEAPI_ENDPOINT +"/"+ subscription +       app_config.MARKETPLACEAPI_API_VERSION,                                      
    'PATCH', 
    request_plan_payload
    )
    return updateresponse
    
def get_sub_operations(subscription):
    sub_operations_data =  call_marketplace_api(  # Use token to call downstream service
        app_config.MARKETPLACEAPI_ENDPOINT +"/"+ subscription + "/operations" + app_config.MARKETPLACEAPI_API_VERSION)
    app.logger.info(sub_operations_data)
    return sub_operations_data

def get_operation(subscriptionid, operationid):
    sub_operation_data =  call_marketplace_api(  # Use token to call downstream service
        app_config.MARKETPLACEAPI_ENDPOINT +"/"+ subscriptionid + "/operations" + "/" + operationid + app_config.MARKETPLACEAPI_API_VERSION)
    app.logger.info(sub_operation_data)
    return sub_operation_data

def get_marketplace_access_token():
    token_url = app_config.AUTHORITY + app_config.MARKETPLACEAPI_TENANTID + '/oauth2/token'
    data = {'grant_type': 'client_credentials', 
            'client_id' : app_config.MARKETPLACEAPI_CLIENT_ID, 
            'client_secret' : app_config.MARKETPLACEAPI_CLIENT_SECRET,  
            'resource':'62d94f6c-d599-489b-a797-3e10e42fbe22'}
    
    api_call_headers = {'content-type': 'application/x-www-form-urlencoded'}
    # get token for market place api
    access_token_response = requests.post(token_url, headers=api_call_headers, data=data).json()
    return access_token_response

def call_marketplace_api(request_url, request_method='GET', request_payload=''):
    
    token = _get_token_from_cache(app_config.SCOPE)
    if not token:
        return redirect(url_for("login"))
    
    # get token for market place api
    access_token_response = get_marketplace_access_token() 
    headers={'Authorization': 'Bearer ' + access_token_response['access_token'],
                            'Content-Type': 'application/json',
                            'x-ms-requestid': str(uuid.uuid4()),
                            'x-ms-correlationid': str(uuid.uuid4())}
    
    if request_method == 'GET':
        reponse_data= requests.get(  # Use token to call downstream service
                        request_url,
                        headers=headers
                        ).json()
        return reponse_data
    elif request_method == 'POST':
        reponse_data=requests.post(  # Use token to call downstream service
                    request_url,
                    headers=headers,
                    data=request_payload,
        ).json()
        return reponse_data
    elif request_method == 'PATCH':
        reponse_data=requests.patch(  # Use token to call downstream service
                    request_url,
                    headers=headers,
                    data=request_payload,
        )
        return reponse_data
    elif request_method == 'DELETE' :
        reponse_data=requests.get(  # Use token to call downstream service
                    request_url,
                    headers=headers
        ).json()
        return reponse_data


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")

