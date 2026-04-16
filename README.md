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
from zapapi import MessageDirection, ZapAPI

with ZapAPI(
    user_data_dir="./userdata/profile/wpp-playwright",
    headless=True,
) as api:
    api.auth.ensure_ready()
    chat = api.chats.get("Eu")
    api.messages.send_text(chat, "teste")
    page = api.messages.history(chat, limit=20)
    result = api.inbox.poll(chats=[chat])

    for message in result.messages:
        if message.direction is MessageDirection.INBOUND:
            print(message.text)
```

## Observacoes

- `ZapAPI(...)` apenas configura a sessao. Use `with ZapAPI(...)`, `api.start()` ou `ZapAPI.connect(...)` para abrir o browser.
- `api.auth.status()` informa `loading`, `qr_required` ou `ready`.
- `api.messages.history(..., before=cursor)` pagina historico usando o `cursor` retornado.
- `api.inbox.listen(...)` entrega resultados explicitos de polling, sem iteracao implicita do objeto principal.
- A API principal continua sendo uma automacao do WhatsApp Web, entao mudancas de DOM ainda podem exigir ajuste de seletores.
