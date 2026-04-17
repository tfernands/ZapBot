# Quickstart

Este projeto usa `uv` para criar o ambiente, instalar dependencias e executar os exemplos.

## 1. Instale o uv

Se voce ainda nao tem `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verifique:

```bash
uv --version
```

## 2. Sincronize o ambiente

Na raiz do projeto:

```bash
uv sync
```

Isso cria `.venv/`, instala o projeto em modo editavel e resolve as dependencias a partir do `pyproject.toml`.

Se voce tambem quiser suporte para notebook/Jupyter:

```bash
uv sync --extra notebook
```

## 3. Instale o Chromium do Playwright

```bash
uv run playwright install chromium
```

## 4. Faça o bootstrap da sessao do WhatsApp

Na primeira execucao, rode em modo visual para escanear o QR Code:

```bash
uv run python examples/playwright_bootstrap.py
```

A sessao sera persistida em:

```text
./userdata/profile/wpp-playwright
```

## 5. Rode o exemplo headless

Depois de autenticar a sessao:

```bash
uv run python examples/playwright_inbox.py
```

## 6. Use a API no projeto

Exemplo minimo:

```bash
uv run python
```

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
    print(page.cursor, page.has_more)

    result = api.inbox.poll(chats=[chat])
    for message in result.messages:
        if message.direction is MessageDirection.INBOUND:
            if isinstance(message, ChatImageMessage):
                print("[imagem]", message.caption or "")
            else:
                print(message.text)
```

O construtor de `ZapAPI` nao abre o browser por conta propria. Use `with ZapAPI(...)`, `api.start()` ou `ZapAPI.connect(...)`.

## 7. Suba o servidor MCP com notificacao pronta

Se voce quiser usar este repositorio como um servidor MCP e disparar um aviso de conclusao no fim de uma tarefa, inicie assim:

```bash
uv run whats-mcp \
  --notify-chat "Eu" \
  --notify-message "Refatoracao concluida."
```

Depois disso, a ferramenta MCP `notify_completion` pode ser chamada sem argumentos.
Se a sessao ainda nao existir, o servidor abre o browser visivel temporariamente para o QR Code e depois volta ao modo headless padrao.
No cliente MCP, prefira registrar esse servidor com o nome `whatsapp`.

Se quiser operar com whitelist e aprovacao explicita antes de enviar mensagens, suba o servidor assim:

```bash
uv run whats-mcp \
  --allow-tool messages_send_text \
  --allow-tool notify_completion \
  --write-chat-allowlist "Eu,Equipe" \
  --read-chat-allowlist "Equipe" \
  --require-approval-for messages_send_text \
  --require-approval-for notify_completion \
  --deny-unfiltered-inbox-poll true \
  --deny-fuzzy-chat-match true
```

Nesse modo, as ferramentas marcadas em `--require-approval-for` precisam receber este bloco no payload:

```json
{
  "approval": {
    "confirm": true,
    "reason": "acao autorizada"
  }
}
```

## Fluxo recomendado para um novo usuario

1. `uv sync`
2. `uv run playwright install chromium`
3. `uv run python examples/playwright_bootstrap.py`
4. `uv run python examples/playwright_inbox.py`

## Troubleshooting

- Se o import de `zapapi` falhar, rode `uv sync` novamente.
- Se o browser abrir mas o WhatsApp nao autenticar, repita o bootstrap com `headless=False`.
- Se o exemplo headless falhar com autenticacao, rode `uv run python examples/playwright_bootstrap.py` e escaneie o QR Code.
- Se a sessao expirar, apague `./userdata/profile/wpp-playwright` e rode o bootstrap de novo.
- O `ZapAPI` encapsula o backend sync do Playwright em uma thread dedicada para evitar falha quando o host do MCP roda com `asyncio`.
- Se quiser reproduzir exatamente as versoes travadas do repositório, use `uv sync --frozen`.
