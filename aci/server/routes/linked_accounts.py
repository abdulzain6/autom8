from typing import Annotated
from authlib.jose import jwt
from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from aci.common.db import crud
from aci.common.db.sql_models import LinkedAccount
from aci.common.enums import SecurityScheme
from aci.common.exceptions import (
    AppConfigurationNotFound,
    AppNotFound,
    AuthenticationError,
    InvalidCredentials,
    LinkedAccountAlreadyExists,
    LinkedAccountNotFound,
    NoImplementationFound,
    OAuth2Error,
)
from aci.common.logging_setup import get_logger
from aci.common.schemas.linked_accounts import (
    LinkedAccountAPIKeyCreate,
    LinkedAccountNoAuthCreate,
    LinkedAccountOAuth2Create,
    LinkedAccountOAuth2CreateState,
    LinkedAccountPublic,
    LinkedAccountsList,
    LinkedAccountUpdate,
    LinkedAccountWithCredentials,
)
from aci.common.schemas.security_scheme import (
    APIKeyScheme,
    APIKeySchemeCredentials,
    NoAuthSchemeCredentials,
)
from aci.server import config
from aci.server import dependencies as deps
from aci.server import security_credentials_manager as scm
from aci.server.function_executors.rest_api_key_function_executor import RestAPIKeyFunctionExecutor
from aci.server.oauth2_manager import OAuth2Manager

router = APIRouter()
logger = get_logger(__name__)

LINKED_ACCOUNTS_OAUTH2_CALLBACK_ROUTE_NAME = "linked_accounts_oauth2_callback"



@router.post("/no-auth", response_model=LinkedAccountPublic)
def link_account_with_no_auth(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    body: LinkedAccountNoAuthCreate,
) -> LinkedAccount:
    """
    Create a linked account under an App that requires no authentication.
    """
    logger.info(
        f"Linking no_auth account, app_name={body.app_name}, "
        f"user_id={context.user.id}"
    )
    app_configuration = crud.app_configurations.get_app_configuration(
        context.db_session, body.app_name
    )
    if not app_configuration:
        logger.error(
            f"Failed to link no_auth account, app configuration not found, app_name={body.app_name}"
        )
        raise AppConfigurationNotFound(
            f"configuration for app={body.app_name} not found"
        )
    if app_configuration.security_scheme != SecurityScheme.NO_AUTH:
        logger.error(
            f"Failed to link no_auth account, app configuration security scheme is not no_auth, "
            f"app_name={body.app_name} security_scheme={app_configuration.security_scheme}"
        )
        raise NoImplementationFound(
            f"the security_scheme configured for app={body.app_name} is "
            f"{app_configuration.security_scheme}, not no_auth"
        )
    linked_account = crud.linked_accounts.get_linked_account(
        context.db_session,
        context.user.id,
        body.app_name,
    )
    if linked_account:
        logger.error(
            f"Failed to link no_auth account, linked account already exists, "
            f"user_id={context.user.id} app_name={body.app_name}"
        )
        raise LinkedAccountAlreadyExists(
            f"linked account with user_id={context.user.id} already exists for app={body.app_name}"
        )
    else:
        logger.info(
            f"Creating no_auth linked account, "
            f"user_id={context.user.id}, "
            f"app_name={body.app_name}"
        )
        linked_account = crud.linked_accounts.create_linked_account(
            context.db_session,
            context.user.id,
            body.app_name,
            SecurityScheme.NO_AUTH,
            NoAuthSchemeCredentials(),
            enabled=True,
        )

    context.db_session.commit()

    return linked_account


@router.post("/api-key", response_model=LinkedAccountPublic)
def link_account_with_api_key(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    body: LinkedAccountAPIKeyCreate,
) -> LinkedAccount:
    """
    Create a linked account for an API key-based App, verifying the credentials first.
    """
    logger.info(
        f"Linking api_key account, app_name={body.app_name}, user_id={context.user.id}"
    )

    # 1. Fetch the App to get security scheme details
    app = crud.apps.get_app(context.db_session, body.app_name, True)
    if not app:
        logger.error(f"Failed to link account, app not found, app_name={body.app_name}")
        raise AppNotFound(f"App with name={body.app_name} not found")

    # 2. Validate that the app uses an API key and get the scheme config
    api_key_scheme_config = app.security_schemes.get(SecurityScheme.API_KEY)
    if not api_key_scheme_config:
        logger.error(
            f"Failed to link account, app does not have an API key security scheme, app_name={body.app_name}"
        )
        raise NoImplementationFound(
            f"API Key security scheme not configured for app={body.app_name}"
        )

    # 3. Prepare credentials for testing
    security_credentials = APIKeySchemeCredentials(secret_key=body.api_key)
    api_key_scheme = APIKeyScheme.model_validate(api_key_scheme_config)

    # 4. Verify credentials using the static test method if a test is configured
    test_config = api_key_scheme.test
    if test_config and test_config.function_name:
        logger.info(
            f"Verifying API key for app={body.app_name} using test function={test_config.function_name}"
        )
        test_function = crud.functions.get_function(
            context.db_session, test_config.function_name, True
        )
        if not test_function:
            logger.error(
                f"Credential test function '{test_config.function_name}' not found for app '{body.app_name}'"
            )
            raise AppConfigurationNotFound(
                f"Test function for {body.app_name} is misconfigured or missing."
            )

        # Call the static method to test the key
        test_result = RestAPIKeyFunctionExecutor.test_credentials(
            test_function=test_function,
            security_scheme=api_key_scheme,
            security_credentials=security_credentials,
        )

        if not test_result.success:
            logger.error(
                f"API key verification failed for app={body.app_name}. Error: {test_result.error}"
            )
            raise InvalidCredentials(
                f"The provided API key for {body.app_name} is invalid. Service responded: {test_result.error}"
            )

        logger.info(f"API key successfully verified for app={body.app_name}")

    # 5. Check if an account already exists
    linked_account = crud.linked_accounts.get_linked_account(
        context.db_session,
        context.user.id,
        body.app_name,
    )
    if linked_account:
        logger.error(
            f"Failed to link api_key account, linked account already exists, user_id={context.user.id} app_name={body.app_name}"
        )
        raise LinkedAccountAlreadyExists(
            f"A linked account for {body.app_name} already exists for this user."
        )

    # 6. Create the new linked account if credentials are valid and no account exists
    logger.info(
        f"Creating api_key linked account, user_id={context.user.id}, app_name={body.app_name}"
    )
    new_linked_account = crud.linked_accounts.create_linked_account(
        db_session=context.db_session,
        user_id=context.user.id,
        app_name=body.app_name,
        security_scheme=SecurityScheme.API_KEY,
        security_credentials=security_credentials,
        enabled=True,
    )

    context.db_session.commit()
    context.db_session.refresh(new_linked_account)

    return new_linked_account


@router.get("/oauth2")
async def link_oauth2_account(
    request: Request,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    query_params: Annotated[LinkedAccountOAuth2Create, Query()],
) -> dict:
    """
    Start an OAuth2 account linking process.
    It will return a redirect url (as a string, instead of RedirectResponse) to the OAuth2 provider's authorization endpoint.
    """
    app_configuration = crud.app_configurations.get_app_configuration(
        context.db_session, query_params.app_name
    )
    if not app_configuration:
        logger.error(
            f"Failed to link OAuth2 account, app configuration not found, "
            f"app_name={query_params.app_name}"
        )
        raise AppConfigurationNotFound(
            f"configuration for app={query_params.app_name} not found."
        )
    # TODO: for now we require the security_schema used for accounts under an App must be the same as the security_schema configured in the app
    # configuration. But in the future, we might lift this restriction and allow any security_schema as long the App supports it.
    if app_configuration.security_scheme != SecurityScheme.OAUTH2:
        logger.error(
            f"Failed to link OAuth2 account, app configuration security scheme is not OAuth2, "
            f"app_name={query_params.app_name} security_scheme={app_configuration.security_scheme}"
        )
        raise NoImplementationFound(
            f"The security_scheme configured in app={query_params.app_name} is "
            f"{app_configuration.security_scheme}, not OAuth2"
        )

    # Enforce linked accounts quota before creating new account
    oauth2_scheme = scm.get_app_configuration_oauth2_scheme(
        app_configuration.app, app_configuration
    )

    oauth2_manager = OAuth2Manager(
        app_name=query_params.app_name,
        client_id=oauth2_scheme.client_id,
        client_secret=oauth2_scheme.client_secret,
        scope=oauth2_scheme.scope,
        authorize_url=oauth2_scheme.authorize_url,
        access_token_url=oauth2_scheme.access_token_url,
        refresh_token_url=oauth2_scheme.refresh_token_url,
        token_endpoint_auth_method=oauth2_scheme.token_endpoint_auth_method,
    )

    path = request.url_for(LINKED_ACCOUNTS_OAUTH2_CALLBACK_ROUTE_NAME).path
    redirect_uri = oauth2_scheme.redirect_url or f"{config.REDIRECT_URI_BASE}{path}"


    oauth2_state = LinkedAccountOAuth2CreateState(
        app_name=query_params.app_name,
        user_id=context.user.id,
        client_id=oauth2_scheme.client_id,
        redirect_uri=redirect_uri,
        code_verifier=OAuth2Manager.generate_code_verifier(),
        after_oauth2_link_redirect_url=query_params.after_oauth2_link_redirect_url,
    )
    oauth2_state_jwt = jwt.encode(
        {"alg": config.JWT_ALGORITHM},
        oauth2_state.model_dump(mode="json", exclude_none=True),
        config.SIGNING_KEY,
    ).decode() 

    authorization_url = await oauth2_manager.create_authorization_url(
        redirect_uri=redirect_uri,
        state=oauth2_state_jwt,
        code_verifier=oauth2_state.code_verifier,
    )
    
    authorization_url = OAuth2Manager.rewrite_oauth2_authorization_url(
        query_params.app_name, authorization_url
    )

    logger.info(f"Linking oauth2 account with authorization_url={authorization_url}")
    return {"url": authorization_url}


@router.get(
    "/oauth2/callback",
    name=LINKED_ACCOUNTS_OAUTH2_CALLBACK_ROUTE_NAME,
    response_model=LinkedAccountWithCredentials,
    response_model_exclude_none=True,
)
async def linked_accounts_oauth2_callback(
    request: Request,
    db_session: Annotated[Session, Depends(deps.yield_db_session)],
) -> LinkedAccount | RedirectResponse:
    """
    Callback endpoint for OAuth2 account linking.
    - A linked account (with necessary credentials from the OAuth2 provider) will be created in the database.
    """
    # check for errors
    error = request.query_params.get("error")
    error_description = request.query_params.get("error_description")
    if error:
        logger.error(
            f"OAuth2 account linking callback received, error={error}, "
            f"error_description={error_description}"
        )
        raise OAuth2Error(
            f"oauth2 account linking callback error: {error}, error_description: {error_description}"
        )

    # check for code
    code = request.query_params.get("code")
    if not code:
        logger.error("OAuth2 account linking callback received, missing code")
        raise OAuth2Error("missing code parameter during account linking")

    # check for state
    state_jwt = request.query_params.get("state")
    if not state_jwt:
        logger.error(
            "OAuth2 account linking callback received, missing state",
        )
        raise OAuth2Error("missing state parameter during account linking")

    # decode the state payload
    try:
        state = LinkedAccountOAuth2CreateState.model_validate(
            jwt.decode(state_jwt, config.SIGNING_KEY)
        )
        logger.info(
            f"OAuth2 account linking callback received, decoded state={state.model_dump(exclude_none=True)}",
        )
    except Exception as e:
        logger.exception(f"Failed to decode OAuth2 state, error={e}")
        raise AuthenticationError("invalid state parameter during account linking") from e

    # check if the app exists
    app = crud.apps.get_app(db_session, state.app_name, False)
    if not app:
        logger.error(
            f"Unable to continue with account linking, app not found app_name={state.app_name}"
        )
        raise AppNotFound(f"app={state.app_name} not found")

    # check app configuration
    # - exists
    # - configuration is OAuth2
    # - client_id matches the one used at the start of the OAuth2 flow
    app_configuration = crud.app_configurations.get_app_configuration(
        db_session,  state.app_name
    )
    if not app_configuration:
        logger.error(
            f"Unable to continue with account linking, app configuration not found "
            f"app_name={state.app_name}"
        )
        raise AppConfigurationNotFound(f"app configuration for app={state.app_name} not found")
    if app_configuration.security_scheme != SecurityScheme.OAUTH2:
        logger.error(
            f"Unable to continue with account linking, app configuration is not OAuth2 "
            f"app_name={state.app_name}"
        )
        raise NoImplementationFound(f"app configuration for app={state.app_name} is not OAuth2")

    # create oauth2 manager
    oauth2_scheme = scm.get_app_configuration_oauth2_scheme(
        app_configuration.app, app_configuration
    )
    if oauth2_scheme.client_id != state.client_id:
        logger.error(
            f"Unable to continue with account linking, client_id of state doesn't match client_id of app configuration "
            f"app_name={state.app_name} "
            f"client_id={oauth2_scheme.client_id} "
            f"state_client_id={state.client_id}"
        )
        raise OAuth2Error("client_id mismatch during account linking")

    oauth2_manager = OAuth2Manager(
        app_name=state.app_name,
        client_id=oauth2_scheme.client_id,
        client_secret=oauth2_scheme.client_secret,
        scope=oauth2_scheme.scope,
        authorize_url=oauth2_scheme.authorize_url,
        access_token_url=oauth2_scheme.access_token_url,
        refresh_token_url=oauth2_scheme.refresh_token_url,
        token_endpoint_auth_method=oauth2_scheme.token_endpoint_auth_method,
    )

    token_response = await oauth2_manager.fetch_token(
        redirect_uri=state.redirect_uri,
        code=code,
        code_verifier=state.code_verifier,
    )
    security_credentials = oauth2_manager.parse_fetch_token_response(token_response)

    # if the linked account already exists, update it, otherwise create a new one
    # TODO: consider separating the logic for updating and creating a linked account or give warning to clients
    # if the linked account already exists to avoid accidental overwriting the account
    # TODO: try/except, retry?
    linked_account = crud.linked_accounts.get_linked_account(
        db_session,
        state.user_id,
        state.app_name,
    )
    if linked_account:
        logger.info(
            f"Updating oauth2 credentials for linked account, linked_account_id={linked_account.id}"
        )
        linked_account = crud.linked_accounts.update_linked_account_credentials(
            db_session, linked_account, security_credentials
        )
    else:
        logger.info(
            f"Creating oauth2 linked account, "
            f"app_name={state.app_name}, "
            f"user_id={state.user_id}"
        )
        linked_account = crud.linked_accounts.create_linked_account(
            db_session,
            state.user_id,
            app_name=state.app_name,
            security_scheme=SecurityScheme.OAUTH2,
            security_credentials=security_credentials,
            enabled=True,
        )
    db_session.commit()

    if state.after_oauth2_link_redirect_url:
        return RedirectResponse(
            url=state.after_oauth2_link_redirect_url, status_code=status.HTTP_302_FOUND
        )

    return linked_account


@router.get("", response_model=list[LinkedAccountPublic])
def list_linked_accounts(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    query_params: Annotated[LinkedAccountsList, Query()],
) -> list[LinkedAccount]:
    """
    List all linked accounts.
    - Optionally filter by app_name and linked_account_owner_id.
    - app_name + linked_account_owner_id can uniquely identify a linked account.
    - This can be an alternatively way to GET /linked-accounts/{linked_account_id} for getting a specific linked account.
    """

    linked_accounts = crud.linked_accounts.get_linked_accounts(
        context.db_session,
        context.user.id,
        query_params.app_name,
    )

    return linked_accounts


@router.get(
    "/{linked_account_id}",
    response_model=LinkedAccountWithCredentials,
    response_model_exclude_none=True,
)
async def get_linked_account(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    linked_account_id: str,
) -> LinkedAccount:
    """
    Get a linked account by its id.
    - linked_account_id uniquely identifies a linked account across the platform.
    """
    logger.info(f"Get linked account, linked_account_id={linked_account_id}")
    # validations
    linked_account = crud.linked_accounts.get_linked_account_by_id_and_user_id(
        context.db_session, context.user.id, linked_account_id
    )
    if not linked_account:
        logger.error(f"Linked account not found, linked_account_id={linked_account_id}")
        raise LinkedAccountNotFound(f"linked account={linked_account_id} not found")

    # Get the app configuration to check and refresh credentials if needed
    app_configuration = crud.app_configurations.get_app_configuration(
        context.db_session, linked_account.app.name
    )
    if not app_configuration:
        logger.error(
            "app configuration not found",
        )
        raise AppConfigurationNotFound(
            f"app configuration for app={linked_account.app.name} not found"
        )

    security_credentials_response = await scm.get_security_credentials(
        linked_account.app, app_configuration, linked_account
    )
    scm.update_security_credentials(
        context.db_session, linked_account.app, linked_account, security_credentials_response
    )
    logger.info(
        f"Fetched security credentials for linked account, linked_account_id={linked_account.id}, "
        f"is_updated={security_credentials_response.is_updated}"
    )
    context.db_session.commit()

    return linked_account


@router.delete("/{linked_account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_linked_account(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    linked_account_id: str,
) -> None:
    """
    Delete a linked account by its id.
    """
    logger.info(f"Delete linked account, linked_account_id={linked_account_id}")
    linked_account = crud.linked_accounts.get_linked_account_by_id_and_user_id(
        context.db_session, context.user.id, linked_account_id
    )
    if not linked_account:
        logger.error(f"Linked account not found, linked_account_id={linked_account_id}")
        raise LinkedAccountNotFound(f"linked account={linked_account_id} not found")

    crud.linked_accounts.delete_linked_account(context.db_session, linked_account)

    context.db_session.commit()


@router.patch("/{linked_account_id}", response_model=LinkedAccountPublic)
def update_linked_account(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    linked_account_id: str,
    body: LinkedAccountUpdate,
) -> LinkedAccount:
    """
    Update a linked account.
    """
    logger.info(f"Update linked account, linked_account_id={linked_account_id}")
    linked_account = crud.linked_accounts.get_linked_account_by_id_and_user_id(
        context.db_session, context.user.id, linked_account_id
    )
    if not linked_account:
        logger.error(f"Linked account not found, linked_account_id={linked_account_id}")
        raise LinkedAccountNotFound(f"Linked account={linked_account_id} not found")

    linked_account = crud.linked_accounts.update_linked_account(
        context.db_session, linked_account, body
    )
    context.db_session.commit()

    return linked_account
