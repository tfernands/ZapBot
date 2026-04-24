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
    "#side div[role='textbox'][data-tab='3']",
    "#side div[role='textbox'][contenteditable='true']",
    "#side div[role='textbox'][aria-label='Pesquisar ou começar uma nova conversa']",
    "#side div[role='textbox'][aria-label='Search input textbox']",
    "#side div[role='textbox'][aria-label='Search or start a new chat']",
    "#side [contenteditable='true'][aria-label*='Pesquisar']",
    "#side [contenteditable='true'][aria-label*='Search']",
    "#side [contenteditable='true'][title*='Pesquisar']",
    "#side [contenteditable='true'][title*='Search']",
    "div[role='textbox'][aria-label='Pesquisar ou começar uma nova conversa']",
    "div[role='textbox'][aria-label='Search input textbox']",
    "div[role='textbox'][aria-label='Search or start a new chat']",
    "[aria-label='Pesquisar ou começar uma nova conversa'][contenteditable='true']",
    "[aria-label='Pesquisar ou começar uma nova conversa'] [contenteditable='true']",
    "[aria-label*='Pesquisar'][contenteditable='true']",
    "[aria-label*='Pesquisar'] [contenteditable='true']",
    "[aria-label*='Search'][contenteditable='true']",
    "[aria-label*='Search'] [contenteditable='true']",
    "input[aria-label*='Pesquisar']",
    "input[aria-label*='Search']",
    "input[placeholder*='Pesquisar']",
    "input[placeholder*='Search']",
    "xpath=//div[@id='side']//div[@role='textbox' and @data-tab='3']",
    "xpath=//div[@id='side']//*[@contenteditable='true' and (contains(@aria-label, 'Pesquisar') or contains(@aria-label, 'Search') or contains(@title, 'Pesquisar') or contains(@title, 'Search'))]",
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
    "#main [role='application']",
    "#main div[tabindex='-1']",
    "xpath=//div[@id='main']//div[contains(@aria-label, 'Lista de mensagens.')]/..",
    "xpath=//div[@id='main']//div[contains(@aria-label, 'message list')]/..",
)

MESSAGE_ROW_SELECTORS = (
    "div.message-in, div.message-out",
    "#main [data-pre-plain-text]",
    "#main .copyable-text[data-pre-plain-text]",
    "div[data-testid='msg-container']",
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

ATTACH_BUTTON_SELECTORS = (
    "footer [aria-label='Anexar']",
    "footer button[title='Anexar']",
    "xpath=//footer//*[@aria-label='Anexar']",
)

IMAGE_FILE_INPUT_SELECTORS = (
    "input[type='file'][accept*='image']",
    "xpath=//input[@type='file' and contains(@accept, 'image')]",
)

MEDIA_SEND_BUTTON_SELECTORS = (
    "button[aria-label='Enviar']",
    "div[role='button'][aria-label='Enviar']",
    "xpath=//*[@aria-label='Enviar']",
)

CHAT_SUMMARY_EVALUATOR = """
node => {
  const clean = (value) =>
    (value || "")
      .replace(/\\u200e/g, "")
      .replace(/\\u200f/g, "")
      .trim();

  // Priority 1: the title attribute of the first span[title] — WhatsApp stores
  // the full, untruncated chat name here even when the visible text is clipped
  // by CSS overflow.  This fixes garbled/truncated names in the sidebar.
  const titleSpan = node.querySelector("span[title]");
  const titleAttr = titleSpan ? clean(titleSpan.getAttribute("title")) : "";

  // Priority 2: fallback to previous heuristic scanning all candidates.
  const candidates = Array.from(
    node.querySelectorAll("span[title], span[dir='auto'], div[dir='auto'], [title]")
  )
    .map((element) => clean(element.getAttribute("title") || element.textContent))
    .filter(Boolean);

  const name = titleAttr || candidates[0] || "";
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
  const isTimeOnly = (value) => /^\\d{1,2}:\\d{2}$/.test(value || "");
  const unique = (values) => {
    const result = [];
    const seen = new Set();
    for (const value of values) {
      if (!value || seen.has(value)) {
        continue;
      }
      result.push(value);
      seen.add(value);
    }
    return result;
  };
  const blockedLabels = new Set([
    "Abrir imagem",
    "Encaminhar mídia",
    "Encaminhar midia",
    "Mensagem citada",
    "Baixar",
    "Editar",
  ]);
  const isLikelyMessageImage = (element) => {
    if (!(element instanceof HTMLImageElement)) {
      return false;
    }

    const src = clean(element.currentSrc || element.src || element.getAttribute("src"));
    if (!src) {
      return false;
    }

    const width = element.naturalWidth || element.width || element.clientWidth || 0;
    const height = element.naturalHeight || element.height || element.clientHeight || 0;
    return width >= 64 || height >= 64 || src.startsWith("blob:") || src.startsWith("data:image/");
  };

  const root =
    node.closest("[data-id]") ||
    node.closest(".message-in, .message-out") ||
    node.closest("[data-pre-plain-text]") ||
    node;
  const quotedContainer = root.querySelector("[aria-label='Mensagem citada']");
  const isInsideQuotedContainer = (element) => Boolean(quotedContainer && quotedContainer.contains(element));
  const preNode =
    (root.matches && root.matches("[data-pre-plain-text]") ? root : null) ||
    root.querySelector("[data-pre-plain-text]") ||
    (node.matches && node.matches("[data-pre-plain-text]") ? node : null) ||
    node.querySelector("[data-pre-plain-text]");
  const textNodes = Array.from(
    root.querySelectorAll("span.selectable-text, [data-testid='selectable-text'], [data-testid='msg-text']")
  ).filter((element) => !isInsideQuotedContainer(element));
  const messageTextNode = textNodes[0] || null;
  const rawTextParts = textNodes
    .map((element) => clean(element.textContent))
    .filter(Boolean);
  const textParts = unique(rawTextParts);
  const captionParts = textParts.filter((value) => !blockedLabels.has(value) && !isTimeOnly(value));
  const quotedTextNode =
    (quotedContainer && quotedContainer.querySelector("[data-testid='selectable-text']")) || null;
  const quotedLines = clean(quotedContainer ? quotedContainer.innerText : "")
    .split("\\n")
    .map((part) => clean(part))
    .filter(Boolean)
    .filter((value) => !blockedLabels.has(value) && !isTimeOnly(value));
  const quotedText =
    clean(quotedTextNode ? quotedTextNode.textContent : "") ||
    (quotedLines.length > 1 ? quotedLines.slice(1).join("\\n") : null);
  const quotedSender =
    (quotedLines.find((value) => value !== quotedText) || quotedLines[0] || null);
  const preferredText =
    clean(messageTextNode ? messageTextNode.innerText : "") ||
    clean(!quotedContainer && preNode ? preNode.innerText : "") ||
    clean(textParts.join("\\n"));
  const fallbackText = clean(root.innerText)
    .split("\\n")
    .map((part) => clean(part))
    .filter(Boolean);
  const normalizedFallbackText = unique(fallbackText).filter(
    (value) =>
      !blockedLabels.has(value) &&
      !isTimeOnly(value) &&
      value !== quotedSender &&
      value !== quotedText
  );
  const openImageControl =
    root.querySelector("[aria-label='Abrir imagem'], [title='Abrir imagem']") ||
    root.querySelector("button[aria-label='Abrir imagem']");
  const imageCandidates = Array.from(root.querySelectorAll("img")).filter(isLikelyMessageImage);
  const previewImage =
    imageCandidates.sort((left, right) => {
      const leftArea = (left.naturalWidth || left.width || left.clientWidth || 0) * (left.naturalHeight || left.height || left.clientHeight || 0);
      const rightArea = (right.naturalWidth || right.width || right.clientWidth || 0) * (right.naturalHeight || right.height || right.clientHeight || 0);
      return rightArea - leftArea;
    })[0] || null;
  const hasImage = Boolean(openImageControl) || imageCandidates.length > 0;
  const captionText = captionParts.join("\\n") || null;
  const text = hasImage ? captionText : (preferredText || normalizedFallbackText.join("\\n"));
  const testId = clean(root.getAttribute("data-testid")) || clean(node.getAttribute("data-testid"));
  const classNames = [];
  let current = root;
  for (let i = 0; current && i < 6; i += 1) {
    classNames.push(typeof current.className === "string" ? current.className : "");
    current = current.parentElement;
  }
  const className = classNames.join(" ");
  const rect = root.getBoundingClientRect();
  const centerX = rect.left + rect.width / 2;
  const senderLabel =
    clean(
      Array.from(root.querySelectorAll("[aria-label]"))
        .map((element) => clean(element.getAttribute("aria-label")))
        .find((value) => value.endsWith(":"))
    ) || null;

  return {
    message_id:
      clean(root.getAttribute("data-id")) ||
      clean(node.getAttribute("data-id")) ||
      clean(root.id) ||
      clean(node.id) ||
      null,
    message_text: text || null,
    caption_text: captionText,
    pre_plain_text: clean(preNode ? preNode.getAttribute("data-pre-plain-text") : "") || null,
    has_image: hasImage,
    preview_url: previewImage ? clean(previewImage.currentSrc || previewImage.src || previewImage.getAttribute("src")) || null : null,
    image_width: previewImage ? previewImage.naturalWidth || previewImage.width || previewImage.clientWidth || null : null,
    image_height: previewImage ? previewImage.naturalHeight || previewImage.height || previewImage.clientHeight || null : null,
    quoted_sender: quotedSender,
    quoted_text: quotedText,
    sender_label: senderLabel,
    outgoing:
      className.includes("message-out") ||
      testId.includes("outgoing") ||
      Boolean(root.querySelector("[data-icon='msg-dblcheck'], [data-icon='msg-check']")) ||
      centerX > window.innerWidth * 0.55,
  };
}
"""
