HOME_READY_SELECTORS = (
    "#pane-side",
    "xpath=//div[@id='pane-side']",
)

QR_CODE_SELECTORS = (
    "xpath=//canvas",
    "xpath=//div[contains(@data-testid, 'qrcode')]",
    "xpath=//div[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'qr')]",
)

SEARCH_BOX_SELECTORS = (
    "#side div[contenteditable='true'][data-tab='3']",
    "#side div[role='textbox'][contenteditable='true']",
    "xpath=//div[@data-tab='3' and @role='textbox']",
    "xpath=//div[@role='textbox' and @contenteditable='true']",
)

CHAT_LIST_ITEM_SELECTORS = (
    "#pane-side div[role='listitem']",
    "xpath=//*[@id='pane-side']//div[@role='listitem']",
    "xpath=//*[@id='pane-side']//div[@aria-label='Lista de conversas' or @aria-label='Resultados da pesquisa.']/div/div/div",
)

OPEN_CHAT_TITLE_SELECTORS = (
    "#main header span[dir='auto']",
    "xpath=//div[@id='main']/header//span[@dir='auto'][1]",
)

MESSAGE_LIST_CONTAINER_SELECTORS = (
    "div[data-testid='conversation-panel-body']",
    "xpath=//div[@id='main']//div[contains(@aria-label, 'Lista de mensagens.')]/..",
    "xpath=//div[@id='main']//div[contains(@aria-label, 'message list')]/..",
)

MESSAGE_ROW_SELECTORS = (
    "div[data-testid='msg-container']",
    "div.message-in, div.message-out",
    "xpath=//div[@id='main']//div[contains(@data-testid, 'msg-container')]",
    "xpath=//div[@id='main']//div[contains(@class, 'message-in') or contains(@class, 'message-out')]",
)

MESSAGE_COMPOSER_SELECTORS = (
    "footer div[role='textbox'][contenteditable='true']",
    "xpath=//footer//div[@role='textbox' and @contenteditable='true']",
    "xpath=//div[@title='Mensagem']",
)

SEND_BUTTON_SELECTORS = (
    "button:has(span[data-icon='send'])",
    "xpath=//button[.//span[@data-icon='send']]",
)

CHAT_SUMMARY_EVALUATOR = """
node => {
  const clean = (value) =>
    (value || "")
      .replace(/\\u200e/g, "")
      .replace(/\\u200f/g, "")
      .trim();

  const candidates = Array.from(
    node.querySelectorAll("span[title], span[dir='auto'], div[dir='auto'], [title]")
  )
    .map((element) => clean(element.getAttribute("title") || element.textContent))
    .filter(Boolean);

  const name = candidates[0] || "";
  const preview = candidates.find((value) => value !== name) || "";
  const innerText = clean(node.innerText);
  const unreadMatch = (clean(node.getAttribute("aria-label")) + " " + innerText).match(
    /(\\d+)\\s+(?:mensagens?\\s+)?n[a\\u00e3]o\\s+lidas?/i
  );

  return {
    name,
    preview: preview || null,
    timestamp: null,
    unread_count: unreadMatch ? Number.parseInt(unreadMatch[1], 10) : 0,
  };
}
"""

MESSAGE_PAYLOAD_EVALUATOR = """
node => {
  const clean = (value) =>
    (value || "")
      .replace(/\\u200e/g, "")
      .replace(/\\u200f/g, "")
      .trim();

  const preNode = node.querySelector("[data-pre-plain-text]");
  const textParts = Array.from(
    node.querySelectorAll(
      "span.selectable-text span, [data-testid='msg-text'] span, div.copyable-text span, span[dir='auto']"
    )
  )
    .map((element) => clean(element.textContent))
    .filter(Boolean);

  const text = clean(textParts.join("\\n")) || clean(node.innerText);
  const testId = clean(node.getAttribute("data-testid"));
  const className = typeof node.className === "string" ? node.className : "";

  return {
    message_id: clean(node.getAttribute("data-id")) || clean(node.id) || null,
    message_text: text || null,
    pre_plain_text: clean(preNode ? preNode.getAttribute("data-pre-plain-text") : "") || null,
    outgoing:
      className.includes("message-out") ||
      testId.includes("outgoing") ||
      Boolean(node.querySelector("[data-icon='msg-dblcheck'], [data-icon='msg-check']")),
  };
}
"""
