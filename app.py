import os
import time
import json
import asyncio
import traceback
from typing import Any, Dict, List, Optional
from aiohttp import web, ClientSession

from botbuilder.core import (
    BotFrameworkAdapterSettings,
    BotFrameworkAdapter,
    TurnContext,
    MemoryStorage,
    UserState,
    ConversationState,
)
from botbuilder.core.integration import aiohttp_error_middleware
from botbuilder.schema import Activity, HeroCard, CardAction, ActionTypes, Attachment, ActivityTypes

from botbuilder.dialogs import (
    DialogSet,
    DialogTurnStatus,
    WaterfallDialog,
    WaterfallStepContext,
    OAuthPrompt,
    OAuthPromptSettings,
)
from botbuilder.dialogs.prompts import PromptOptions

# =========================
# Azure Bot / Teams
# =========================
APP_ID = os.environ.get("MicrosoftAppId", "")
APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
APP_TYPE = os.environ.get("MicrosoftAppType", "MultiTenant")
APP_TENANT_ID = os.environ.get("MicrosoftAppTenantId", "")

# =========================
# Databricks / Genie
# =========================
OAUTH_CONNECTION_NAME = os.environ.get("OAUTH_CONNECTION_NAME", "databricksSSO")
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "86400"))
MAX_TABLE_ROWS = int(os.environ.get("MAX_TABLE_ROWS", "20"))
MAX_TABLE_COLS = int(os.environ.get("MAX_TABLE_COLS", "8"))
DEBUG_FULL_RESPONSE = False

settings = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
if APP_TYPE == "SingleTenant" and APP_TENANT_ID:
    settings.channel_auth_tenant = APP_TENANT_ID

adapter = BotFrameworkAdapter(settings)

memory = MemoryStorage()
user_state = UserState(memory)
conversation_state = ConversationState(memory)

conversation_data_accessor = conversation_state.create_property("GenieConversationData")
dialog_state_accessor = conversation_state.create_property("DialogState")

dialogs = DialogSet(dialog_state_accessor)

# =========================
# Dialogs
# =========================
dialogs.add(
    OAuthPrompt(
        "OAuthPrompt",
        OAuthPromptSettings(
            connection_name=OAUTH_CONNECTION_NAME,
            title="Login no Genie",
            text="Faça login no Genie para consultar apenas os dados que você tem permissão para ver.",
            timeout=300000,
        ),
    )
)

async def login_step(step_context: WaterfallStepContext):
    return await step_context.begin_dialog("OAuthPrompt", PromptOptions())

async def process_login_step(step_context: WaterfallStepContext):
    token_response = step_context.result

    if not token_response or not token_response.token:
        await step_context.context.send_activity("Não consegui concluir o login no Genie.")
        return await step_context.end_dialog(None)

    await step_context.context.send_activity("✅ Login no Genie concluído. Agora envie uma pergunta para o Genie.")
    await step_context.context.send_activity(
        Activity(type="message", attachments=[create_main_card(logged_in=True)])
    )
    return await step_context.end_dialog(token_response.token)

dialogs.add(WaterfallDialog("LoginDialog", [login_step, process_login_step]))

# =========================
# Helpers
# =========================
def now_ts() -> int:
    return int(time.time())

def reset_genie_session(user_data: dict):
    user_data["genie_conversation_id"] = None
    user_data["last_activity_ts"] = 0

def is_genie_session_expired(user_data: dict) -> bool:
    last_activity_ts = user_data.get("last_activity_ts", 0)
    if not last_activity_ts:
        return True
    return (now_ts() - last_activity_ts) > SESSION_TTL_SECONDS

def normalize_user_text(text: str) -> str:
    return (text or "").strip()

def is_login_command(command: str) -> bool:
    return command in ("login_genie", "login", "entrar")

def make_action(title: str, action_name: str) -> CardAction:
    return CardAction(
        type=ActionTypes.message_back,
        title=title,
        text="",
        display_text="",
        value={"action": action_name},
    )

def create_main_card(logged_in: bool) -> Attachment:
    buttons = []

    if not logged_in:
        buttons.append(make_action("🔐 Login no Genie", "login_genie"))

    buttons.extend(
        [
            make_action("🔄 Iniciar Nova Sessão", "nova_sessao_genie"),
            make_action("🚪 Logout Genie", "logout_genie"),
        ]
    )

    text = (
        "Você está conectado ao Genie. Faça sua pergunta."
        if logged_in
        else "Faça login no Genie para consultar apenas os dados que você tem permissão para ver."
    )

    card = HeroCard(text=text, buttons=buttons)
    return Attachment(
        content_type="application/vnd.microsoft.card.hero",
        content=card,
    )

def extract_command(turn_context: TurnContext) -> str:
    raw_value = turn_context.activity.value
    if isinstance(raw_value, dict):
        action = raw_value.get("action")
        if isinstance(action, str) and action.strip():
            return action.strip().lower()

    text = normalize_user_text(turn_context.activity.text or "")
    return text.lower()

async def get_existing_token(turn_context: TurnContext):
    token_response = await adapter.get_user_token(turn_context, OAUTH_CONNECTION_NAME, None)
    return token_response.token if token_response and token_response.token else None

async def begin_login(turn_context: TurnContext):
    dialog_context = await dialogs.create_context(turn_context)
    await dialog_context.begin_dialog("LoginDialog")
    await conversation_state.save_changes(turn_context)

async def continue_login_dialog(turn_context: TurnContext):
    dialog_context = await dialogs.create_context(turn_context)
    result = await dialog_context.continue_dialog()

    if result.status == DialogTurnStatus.Complete:
        return result.result

    token = await get_existing_token(turn_context)
    return token

def safe_json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)

def stringify_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return safe_json_dumps(value)

def sanitize_markdown_cell(value: Any) -> str:
    text = stringify_cell(value).replace("\n", " ").replace("\r", " ").strip()
    text = text.replace("|", "\\|")
    return text[:300] if len(text) > 300 else text

def format_markdown_table(headers: List[str], rows: List[List[Any]]) -> str:
    headers = headers[:MAX_TABLE_COLS]
    clipped_rows = [row[:MAX_TABLE_COLS] for row in rows[:MAX_TABLE_ROWS]]

    header_line = "| " + " | ".join(sanitize_markdown_cell(h) or "coluna" for h in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"

    body = []
    for row in clipped_rows:
        padded = row + [""] * (len(headers) - len(row))
        body.append("| " + " | ".join(sanitize_markdown_cell(cell) for cell in padded[:len(headers)]) + " |")

    table = "\n".join([header_line, separator] + body)

    if len(rows) > MAX_TABLE_ROWS:
        table += f"\n\n_Mostrando {MAX_TABLE_ROWS} de {len(rows)} linhas._"

    return table

def extract_text_from_attachment(att: Dict[str, Any]) -> List[str]:
    texts: List[str] = []

    txt = att.get("text")
    if isinstance(txt, str) and txt.strip():
        texts.append(txt.strip())
    elif isinstance(txt, dict):
        txt_content = txt.get("content")
        if isinstance(txt_content, str) and txt_content.strip():
            texts.append(txt_content.strip())

    content = att.get("content")
    if isinstance(content, dict):
        inner_txt = content.get("text")
        if isinstance(inner_txt, str) and inner_txt.strip():
            texts.append(inner_txt.strip())
        elif isinstance(inner_txt, dict):
            inner_txt_content = inner_txt.get("content")
            if isinstance(inner_txt_content, str) and inner_txt_content.strip():
                texts.append(inner_txt_content.strip())

        body = content.get("body")
        if isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    maybe_text = item.get("text")
                    if isinstance(maybe_text, str) and maybe_text.strip():
                        texts.append(maybe_text.strip())
                    elif isinstance(maybe_text, dict):
                        maybe_text_content = maybe_text.get("content")
                        if isinstance(maybe_text_content, str) and maybe_text_content.strip():
                            texts.append(maybe_text_content.strip())

    return texts

def extract_attachment_id(att: Dict[str, Any]) -> Optional[str]:
    candidates = [
        att.get("attachment_id"),
        att.get("attachmentId"),
        att.get("id"),
    ]

    content = att.get("content")
    if isinstance(content, dict):
        candidates.extend([
            content.get("attachment_id"),
            content.get("attachmentId"),
            content.get("id"),
        ])

    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()

    return None

def is_query_attachment(att: Dict[str, Any]) -> bool:
    return isinstance(att.get("query"), dict)

def parse_statement_response(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    statement_response = payload.get("statement_response")
    if not isinstance(statement_response, dict):
        return None

    manifest = statement_response.get("manifest") or {}
    schema = manifest.get("schema") or {}
    columns = schema.get("columns") or []
    result = statement_response.get("result") or {}
    data_array = result.get("data_array") or []

    headers = []
    for idx, col in enumerate(columns):
        if isinstance(col, dict):
            headers.append(str(col.get("name") or f"col_{idx+1}"))
        else:
            headers.append(f"col_{idx+1}")

    rows = []
    if isinstance(data_array, list):
        for row in data_array:
            if isinstance(row, list):
                rows.append(row)
            else:
                rows.append([row])

    return {
        "headers": headers,
        "rows": rows,
        "status": ((statement_response.get("status") or {}).get("state")),
    }

# =========================
# Genie API
# =========================
async def genie_start_conversation(access_token: str, question: str):
    question = normalize_user_text(question)
    if not question:
        raise ValueError("Field 'content' is required and cannot be empty.")

    url = f"{DATABRICKS_HOST}/api/2.0/genie/spaces/{GENIE_SPACE_ID}/start-conversation"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"content": question}

    async with ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise Exception(f"Erro Genie start: {resp.status} - {safe_json_dumps(data)}")
            return data

async def genie_send_message(access_token: str, conversation_id: str, question: str):
    question = normalize_user_text(question)
    if not question:
        raise ValueError("Field 'content' is required and cannot be empty.")

    url = f"{DATABRICKS_HOST}/api/2.0/genie/spaces/{GENIE_SPACE_ID}/conversations/{conversation_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"content": question}

    async with ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise Exception(f"Erro Genie message: {resp.status} - {safe_json_dumps(data)}")
            return data

async def genie_poll_message(access_token: str, conversation_id: str, message_id: str):
    url = f"{DATABRICKS_HOST}/api/2.0/genie/spaces/{GENIE_SPACE_ID}/conversations/{conversation_id}/messages/{message_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    delay = 1
    started = now_ts()

    async with ClientSession() as session:
        while True:
            async with session.get(url, headers=headers, timeout=60) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    raise Exception(f"Erro Genie poll: {resp.status} - {safe_json_dumps(data)}")

                status = data.get("status")
                if status in ("COMPLETED", "FAILED", "CANCELLED", "EXECUTING_QUERY"):
                    return data

            if now_ts() - started > 120:
                raise Exception("Timeout aguardando resposta final do Genie.")

            await asyncio.sleep(delay)
            delay = min(delay + 1, 5)

async def genie_get_query_result(access_token: str, conversation_id: str, message_id: str, attachment_id: str):
    url = (
        f"{DATABRICKS_HOST}/api/2.0/genie/spaces/{GENIE_SPACE_ID}"
        f"/conversations/{conversation_id}/messages/{message_id}/attachments/{attachment_id}/query-result"
    )
    headers = {"Authorization": f"Bearer {access_token}"}

    async with ClientSession() as session:
        async with session.get(url, headers=headers, timeout=60) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise Exception(f"Erro Genie query-result: {resp.status} - {safe_json_dumps(data)}")
            return data

def extract_genie_text(message_data: dict) -> str:
    attachments = message_data.get("attachments") or []
    texts: List[str] = []

    for att in attachments:
        if isinstance(att, dict):
            texts.extend(extract_text_from_attachment(att))

    texts = [t for t in texts if isinstance(t, str) and t.strip()]

    if texts:
        return "\n\n".join(texts)

    error = message_data.get("error")
    if error:
        return f"Erro do Genie: {error}"

    return "O Genie concluiu a solicitação, mas não retornou texto visível."

async def render_genie_response(access_token: str, conversation_id: str, message_id: str, final_message: dict) -> str:
    base_text = extract_genie_text(final_message)
    attachments = final_message.get("attachments") or []

    best_table = None

    for att in attachments:
        if not isinstance(att, dict):
            continue

        if not is_query_attachment(att):
            continue

        attachment_id = extract_attachment_id(att)
        if not attachment_id:
            continue

        try:
            query_payload = await genie_get_query_result(access_token, conversation_id, message_id, attachment_id)
            parsed = parse_statement_response(query_payload)
            if not parsed:
                continue

            headers = parsed["headers"]
            rows = parsed["rows"]

            if not headers or not rows:
                continue

            best_table = format_markdown_table(headers, rows)
            break
        except Exception:
            continue

    parts = []
    if base_text:
        parts.append(base_text)
    if best_table:
        parts.append(best_table)

    return "\n\n".join(parts).strip()

# =========================
# Routes
# =========================
async def messages(req: web.Request) -> web.Response:
    if "application/json" not in req.headers.get("Content-Type", ""):
        return web.Response(status=415)

    body = await req.json()
    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    async def call_bot(turn_context: TurnContext):
        # 1. Processar qualquer diálogo de login em andamento
        # Vital para capturar o evento 'invoke' quando o usuário volta da tela de login
        token = await continue_login_dialog(turn_context)

        # 2. FILTRO DE EVENTOS: Ignorar atualizações de conversa, digitação, etc.
        # Se não for uma mensagem real (ActivityTypes.message), encerramos o turno aqui.
        if turn_context.activity.type != ActivityTypes.message:
            await conversation_state.save_changes(turn_context)
            await user_state.save_changes(turn_context)
            return

        # 3. A partir daqui, temos a certeza de que é uma mensagem do usuário
        text = normalize_user_text(turn_context.activity.text or "")
        command = extract_command(turn_context)
        user_data = await conversation_data_accessor.get(turn_context, lambda: {})

        if token and command == "login_genie":
            await conversation_state.save_changes(turn_context)
            await user_state.save_changes(turn_context)
            return

        if command == "logout_genie":
            await adapter.sign_out_user(turn_context, OAUTH_CONNECTION_NAME)
            reset_genie_session(user_data)
            await turn_context.send_activity("🚪 Você saiu do Genie.")
            await turn_context.send_activity(
                Activity(type="message", attachments=[create_main_card(logged_in=False)])
            )
            await conversation_state.save_changes(turn_context)
            await user_state.save_changes(turn_context)
            return

        if command == "nova_sessao_genie":
            reset_genie_session(user_data)
            existing_token = await get_existing_token(turn_context)
            await turn_context.send_activity("🧹 Nova sessão iniciada. A próxima pergunta começará uma nova conversa no Genie.")
            await turn_context.send_activity(
                Activity(type="message", attachments=[create_main_card(logged_in=bool(existing_token))])
            )
            await conversation_state.save_changes(turn_context)
            await user_state.save_changes(turn_context)
            return

        if is_login_command(command):
            if token:
                await turn_context.send_activity("✅ Você já está logado no Genie. Agora envie uma pergunta.")
                await turn_context.send_activity(
                    Activity(type="message", attachments=[create_main_card(logged_in=True)])
                )
            else:
                await begin_login(turn_context)
            return

        if not token:
            token = await get_existing_token(turn_context)

        if not token:
            await turn_context.send_activity("🔐 Você precisa fazer login no Genie antes de consultar os dados.")
            await turn_context.send_activity(
                Activity(type="message", attachments=[create_main_card(logged_in=False)])
            )
            await conversation_state.save_changes(turn_context)
            await user_state.save_changes(turn_context)
            return

        if not text:
            await turn_context.send_activity(
                Activity(type="message", attachments=[create_main_card(logged_in=True)])
            )
            await conversation_state.save_changes(turn_context)
            await user_state.save_changes(turn_context)
            return

        if is_genie_session_expired(user_data):
            reset_genie_session(user_data)

        await turn_context.send_activity("🔍 Consultando o Genie...")

        try:
            conversation_id = user_data.get("genie_conversation_id")

            if not conversation_id:
                start_data = await genie_start_conversation(token, text)
                conversation_id = start_data["conversation"]["id"]
                message_id = start_data["message"]["id"]
                user_data["genie_conversation_id"] = conversation_id
            else:
                msg_data = await genie_send_message(token, conversation_id, text)
                message_id = msg_data["id"]

            final_message = await genie_poll_message(token, conversation_id, message_id)
            rendered = await render_genie_response(token, conversation_id, message_id, final_message)
            user_data["last_activity_ts"] = now_ts()

            await turn_context.send_activity(rendered)
            await turn_context.send_activity(
                Activity(type="message", attachments=[create_main_card(logged_in=True)])
            )

        except Exception as e:
            await turn_context.send_activity(f"Erro ao consultar o Genie: {str(e)}")

        await conversation_state.save_changes(turn_context)
        await user_state.save_changes(turn_context)

    try:
        await adapter.process_activity(activity, auth_header, call_bot)
        return web.Response(status=201)
    except Exception as e:
        print(f"Erro interno tipo={type(e).__name__} detalhe={repr(e)}")
        traceback.print_exc()
        return web.Response(status=500, text=str(e))

async def index(req: web.Request) -> web.Response:
    return web.Response(text="🚀 Bot do Genie está ONLINE.", status=200)

app = web.Application(middlewares=[aiohttp_error_middleware])
app.router.add_get("/", index)
app.router.add_get("/api/messages", index)
app.router.add_post("/api/messages", messages)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("WEBSITES_PORT", "8000")))
    print(f"Iniciando servidor na porta {port}...")
    web.run_app(app, host="0.0.0.0", port=port)