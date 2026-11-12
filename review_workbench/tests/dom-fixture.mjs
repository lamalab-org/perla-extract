// Small DOM contract for imported editor tests. Browser layout is checked separately.
export function editorDocument() {
  const byId = new Map();
  class Node {
    constructor(tag = "div") {
      this.tag = tag; this.children = []; this.listeners = {}; this.hidden = false;
      this.attributes = {}; this.dataset = {}; this.className = "";
      this.classList = { toggle() {} };
    }
    set id(id) { this._id = id; byId.set(id, this); }
    get id() { return this._id; }
    set value(value) { this._value = String(value); }
    get value() {
      return this._value ?? (this.tag === "select"
        ? (this.children.find(child => child.selected) || this.children[0])?.value ?? "" : "");
    }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    fire(name) { return this.listeners[name]?.({ target: this, currentTarget: this }); }
    append(...children) { this.children.push(...children.filter(Boolean)); }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    setAttribute(name, value) { this.attributes[name] = value; }
    closest() { return this; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() { this.focused = true; }
    scrollTo() {}
    querySelector() { return null; }
  }
  const document = {
    getElementById(id) {
      if (!byId.has(id)) { const node = new Node(); node.id = id; }
      return byId.get(id);
    },
    createTextNode: text => ({ textContent: text, children: [] }),
    querySelector: selector => document.getElementById(selector),
    querySelectorAll: () => [],
  };
  const element = (tag, options = {}, children = []) => {
    const node = new Node(tag);
    if (options.text != null) node.textContent = options.text;
    if (options.className) node.className = options.className;
    Object.assign(node, options.properties || {});
    for (const [key, value] of Object.entries(options.attributes || {})) node.setAttribute(key, value);
    for (const [key, callback] of Object.entries(options.events || {})) node.addEventListener(key, callback);
    node.append(...children);
    return node;
  };
  return { document, element };
}

export const descendants = node => [node, ...(node.children || []).flatMap(descendants)];
