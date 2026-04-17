# ZapAPI
WhatsApp Web API over Playwright.

O onboarding recomendado esta em [quickstart.md](./quickstart.md).

## API principal

A implementacao principal agora expoe servicos explicitos para autenticacao, chats, mensagens e inbox sobre `Playwright + Chromium + contexto persistente`.

## Instalar com uv

```bash
uv sync
uv run playwright install chromium
```

## Bootstrap da sessao

Na primeira execucao, rode em modo visual para escanear o QR Code e persistir a sessao no diretorio de perfil:

```bash
uv run python examples/playwright_bootstrap.py
```

## Execucao headless

Depois que a sessao estiver salva no mesmo `user_data_dir`, rode o exemplo de leitura headless:

```bash
uv run python examples/playwright_inbox.py
```

## API principal em codigo

```python
from zapapi import ChatImageMessage, MessageDirection, ZapAPI

with ZapAPI(
    user_data_dir="./userdata/profile/wpp-playwright",
    headless=True,
) as api:
    api.auth.ensure_ready()
    chat = api.chats.get("Eu")
    api.messages.send_text(chat, "teste")
    api.messages.send_image(chat, "/tmp/foto.png")
    page = api.messages.history(chat, limit=20)
    result = api.inbox.poll(chats=[chat])

    for message in result.messages:
        if message.direction is MessageDirection.INBOUND:
            if isinstance(message, ChatImageMessage):
                print("[imagem]", message.caption or "")
            else:
                print(message.text)
```

## Observacoes

- `ZapAPI(...)` apenas configura a sessao. Use `with ZapAPI(...)`, `api.start()` ou `ZapAPI.connect(...)` para abrir o browser.
- O `ZapAPI` isola o backend sync do Playwright em uma thread dedicada, entao ele pode ser chamado de runtimes com `asyncio` ativo sem disparar o erro `Playwright Sync API inside the asyncio loop`.
- `api.auth.status()` informa `loading`, `qr_required` ou `ready`.
- `api.messages.send_image(chat, image_path)` envia imagem pelo fluxo de anexo do WhatsApp Web.
- `api.messages.history(..., before=cursor)` pagina historico usando o `cursor` retornado.
- `api.inbox.listen(...)` entrega resultados explicitos de polling, sem iteracao implicita do objeto principal.
- A API principal continua sendo uma automacao do WhatsApp Web, entao mudancas de DOM ainda podem exigir ajuste de seletores.

## Servidor MCP

O repositorio agora inclui um servidor MCP `stdio` que expoe a `ZapAPI` como ferramentas MCP.

### Ferramentas disponiveis

- `auth_status`
- `auth_ensure_ready`
- `chats_current`
- `chats_list`
- `chats_get`
- `notify_completion`
- `messages_send_text`
- `messages_send_image`
- `messages_history`
- `inbox_poll`
- `session_close`

### Subir o servidor

Depois de sincronizar o ambiente e instalar o Chromium:

```bash
uv run whats-mcp
```

Se a sessao ainda nao existir, o servidor abre temporariamente um Chromium visivel para o QR Code e, depois da autenticacao, reinicia respeitando o `--headless` final. O padrao continua sendo `true`.

Se voce quiser manter o browser visivel desde o inicio, rode com `--headless false`:

```bash
uv run whats-mcp --headless false
```

Para deixar uma notificacao de conclusao pronta para uso, configure um chat padrao no boot:

```bash
uv run whats-mcp \
  --notify-chat "Eu" \
  --notify-message "Refatoracao concluida."
```

Para subir o servidor com whitelist e gate de aprovacao explicita:

```bash
uv run whats-mcp \
  --allow-tool chats_list \
  --allow-tool messages_send_text \
  --allow-tool notify_completion \
  --write-chat-allowlist "Eu,Equipe" \
  --read-chat-allowlist "Equipe" \
  --allow-image-dir ./artifacts \
  --require-approval-for messages_send_text \
  --require-approval-for notify_completion \
  --deny-unfiltered-inbox-poll true \
  --deny-fuzzy-chat-match true
```

### Exemplo de configuracao MCP

Exemplo generico para um cliente MCP que aceite `command` + `args`:

```json
{
  "mcpServers": {
    "whatsapp": {
      "command": "uv",
      "args": [
        "run",
        "whats-mcp",
        "--headless",
        "true"
      ]
    }
  }
}
```

### Observacoes do MCP

- Recomenda-se registrar este servidor como `whatsapp` no cliente MCP.
- O browser Playwright e aberto de forma lazy, na primeira chamada de ferramenta que precisa da sessao.
- O `user_data_dir` padrao e `./userdata/profile/wpp-playwright`; use `--user-data-dir` apenas se quiser outro perfil.
- Chamadas barradas pela whitelist, pelas allowlists ou pela falta de `approval.confirm=true` falham antes de inicializar a sessao do `ZapAPI`.
- Use `auth_status` ou `auth_ensure_ready` antes de enviar mensagens. Se a sessao ainda nao existir e o servidor estiver em headless, ele abre uma autenticacao visivel temporaria automaticamente.
- Quando `--allow-tool` estiver ativo, `auth_status` e `auth_ensure_ready` sao incluidos automaticamente na whitelist.
- Os argumentos `chat` aceitam tanto string quanto objetos `{ "name": "...", "key": "..." }` retornados por outras ferramentas.
- `notify_completion` envia uma mensagem curta para o chat configurado via `--notify-chat`; se preferir, tambem aceita `chat` e `text` diretamente na chamada.
- `--allow-tool` controla quais ferramentas aparecem em `tools/list` e quais podem ser executadas.
- `--read-chat-allowlist` e `--write-chat-allowlist` bloqueiam acesso a chats fora da politica. `chats_list` tambem fica filtrado por essa visibilidade.
- `--require-approval-for` exige que a chamada da ferramenta inclua `approval.confirm=true`.
- `--allow-image-dir` restringe `messages_send_image` a diretorios locais aprovados.
- `--deny-unfiltered-inbox-poll true` bloqueia `inbox_poll` sem `arguments.chats`.
- `--deny-fuzzy-chat-match true` bloqueia `chats_get` com `exact_match=false`.
- `session_close` fecha o browser mantido por esse processo MCP.

### Exemplo de aprovacao explicita

Quando uma ferramenta estiver em `--require-approval-for`, o cliente precisa enviar algo assim:

```json
{
  "name": "messages_send_text",
  "arguments": {
    "chat": "Equipe",
    "text": "Deploy finalizado.",
    "approval": {
      "confirm": true,
      "reason": "envio autorizado pelo operador"
    }
  }
}
```
