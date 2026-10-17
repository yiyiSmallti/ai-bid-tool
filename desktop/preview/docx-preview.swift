// bid-docx-preview renders a DOCX to an A4 PDF for export page previews:
// docx-preview lays the document out as HTML in WebKit, which prints it to pages.
// Printing waits for fonts and a short pause: a system font loaded on first use, such
// as 黑体 in a heading, otherwise leaves every page blank.
// Usage: bid-docx-preview IN.docx OUT.pdf. The scripts sit next to the binary.
// Exit 0 writes the PDF; any failure prints one line to stderr and exits non-zero.
import AppKit
import WebKit

let args = CommandLine.arguments
func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}
guard args.count == 3 else { fail("usage: bid-docx-preview IN.docx OUT.pdf", 2) }
let here = URL(fileURLWithPath: args[0]).resolvingSymlinksInPath().deletingLastPathComponent()
func script(_ name: String) -> String {
    guard let text = try? String(contentsOf: here.appendingPathComponent(name), encoding: .utf8) else {
        fail("missing \(name)", 2)
    }
    return text
}
guard let docx = try? Data(contentsOf: URL(fileURLWithPath: args[1])) else { fail("cannot read input", 2) }

// The CSP keeps the page offline: nothing the document names is fetched.
// Page margins come from the print margins, so sections drop their own top and
// bottom padding and every printed page keeps the same space. Each section is a
// page docx-preview split at a page break, so printing starts each on a new page.
// docx-preview drops keep-with-next, so each run of such paragraphs is wrapped
// with the block after it, and a group that would cross a printed page is
// pushed down to the next one, as Word keeps them together. WebKit clips a
// block that a page break (break-inside or break-before) moves to the next
// page, so a spacer before the group reaches the page end instead.
// A table is split the same way: at the last whole row that fits, the rest
// becomes a new table on the next page that opens with the header rows
// (w:tblHeader), so no row is cut and each page's part is closed.
let html = """
<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data: blob:">
<style>
body { margin: 0; background: white }
section.docx { min-height: 0 !important; height: auto !important; padding-top: 0 !important;
  padding-bottom: 0 !important; margin: 0 !important; box-shadow: none !important }
section.docx + section.docx { break-before: page }
</style>
<script>\(script("jszip.min.js"))</script><script>\(script("docx-preview.min.js"))</script>
</head><body><div id="out"></div><script>
const bytes = Uint8Array.from(atob("\(docx.base64EncodedString())"), c => c.charCodeAt(0));
const options = {inWrapper: false, breakPages: true, ignoreLastRenderedPageBreak: true, useBase64URL: true};
function keep(element) {
  return element && element.tagName === "P" && element.style.breakAfter === "avoid";
}
// Printed page height in CSS pixels: the page width is scaled to the paper.
const height = (841.9 - 56 * 2) / (595.3 / 794);
// The end of the page `top` is on; a block within a pixel of a page start is on that page.
function pageEnd(origin, top) {
  return origin + (Math.floor((top - origin + 1) / height) + 1) * height;
}
// Whether a cell merged down from an earlier row reaches row `at`.
function spanned(rows, at) {
  return rows.slice(0, at).some((row, index) =>
    [...row.cells].some(cell => cell.style.display !== "none" && index + cell.rowSpan > at));
}
// A block of the given height before `element`; a margin would collapse into the
// margin of the paragraph above and not move it.
function space(element, size) {
  const spacer = document.createElement("div");
  spacer.style.height = Math.max(0, size) + "px";
  element.before(spacer);
}
// Print wraps a narrow cell slightly differently from the screen, sometimes a line
// more, so a table part leaves about one line free at the page end.
const slack = 24;
// A part starts just below the page start, so its top border does not show at the
// bottom of the page before.
const edge = 6;
function split(table, origin) {
  for (let part = table, tries = table.rows.length; tries > 0; tries--) {
    const rows = [...part.rows];
    const header = rows.findIndex(row => !row.dataset.header);
    if (header < 0) return;
    const top = part.getBoundingClientRect().top;
    const end = pageEnd(origin, top);
    const cut = rows.findIndex((row, index) =>
      index >= header && row.getBoundingClientRect().bottom > end - slack);
    if (cut < 0) return;
    let at = cut;
    while (at > header && spanned(rows, at)) at--;
    if (at === header) {
      // Not even the first row fits under the header: the table starts on the next
      // page, unless it already starts a page and the row is taller than one.
      if (top - (end - height) < edge + 2) return;
      space(part, end - top + edge);
      continue;
    }
    const next = part.cloneNode(false);
    for (const child of part.children)
      if (child.tagName === "COLGROUP") next.append(child.cloneNode(true));
    for (const row of rows.slice(0, header)) next.append(row.cloneNode(true));
    for (const row of rows.slice(at)) next.append(row);
    part.after(next);
    space(next, end - part.getBoundingClientRect().bottom + edge);
    part = next;
  }
}
function headers(items) {
  for (const item of items)
    if (item.type === "table")
      for (const row of item.children)
        if (row.isHeader) row.cssStyle = {...row.cssStyle, "$data-header": "1"};
}
docx.parseAsync(bytes, options)
  .then(parsed => {
    for (const item of parsed.documentPart.body.children)
      if (item.type === "paragraph" && item.keepNext)
        item.cssStyle = {...item.cssStyle, breakAfter: "avoid"};
    headers(parsed.documentPart.body.children);
    return docx.renderDocument(parsed, document.getElementById("out"), null, options);
  })
  // Pages are measured once the fonts are in: a font loaded later changes line heights.
  .then(() => document.fonts.ready)
  .then(() => new Promise(done => setTimeout(done, 200)))
  .then(() => {
    for (const first of [...document.querySelectorAll("p")]) {
      if (!keep(first) || keep(first.previousElementSibling)) continue;
      const group = document.createElement("div");
      group.className = "keep";
      first.before(group);
      let next = first;
      do { const after = next.nextElementSibling; group.append(next); next = after; }
      while (keep(group.lastElementChild) && next);
    }
    let origin = 0;
    for (const block of document.querySelectorAll("section.docx, article > *")) {
      const box = block.getBoundingClientRect();
      const style = getComputedStyle(block);
      if (block.tagName === "SECTION" || style.breakBefore === "page") {
        // A page a forced break starts keeps the block's top margin; a section's first
        // paragraph margin collapses out of the section on screen but not in print.
        const first = block.tagName === "SECTION" ? block.querySelector("article > *") : block;
        origin = first ? Math.min(box.top, first.getBoundingClientRect().top
          - parseFloat(getComputedStyle(first).marginTop)) : box.top;
        continue;
      }
      if (block.className === "keep") {
        const end = pageEnd(origin, box.top);
        if (box.bottom > end && box.height < height) {
          space(block, end - box.top);
          origin = end;
        }
      }
      const table = block.tagName === "TABLE" ? block
        : block.className === "keep" && block.lastElementChild?.tagName === "TABLE"
          ? block.lastElementChild : null;
      if (table) split(table, origin);
    }
  })
  .then(() => webkit.messageHandlers.done.postMessage("ok"),
        e => webkit.messageHandlers.done.postMessage("render failed: " + e));
</script></body></html>
"""

final class Printer: NSObject, WKScriptMessageHandler {
    var view: WKWebView!
    var window: NSWindow!

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard (message.body as? String) == "ok" else { fail("\(message.body)", 3) }
        let info = NSPrintInfo()
        info.paperSize = NSSize(width: 595.3, height: 841.9)
        info.topMargin = 56
        info.bottomMargin = 56
        info.leftMargin = 0
        info.rightMargin = 0
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.jobDisposition = .save
        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = URL(fileURLWithPath: args[2]).absoluteURL
        let operation = view.printOperation(with: info)
        operation.showsPrintPanel = false
        operation.showsProgressPanel = false
        operation.view?.frame = view.bounds
        operation.runModal(for: window, delegate: self, didRun: #selector(printed(_:success:context:)), contextInfo: nil)
    }

    @objc func printed(_ operation: NSPrintOperation, success: Bool, context: UnsafeMutableRawPointer?) {
        if !success { fail("print failed", 4) }
        exit(0)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let printer = Printer()
let config = WKWebViewConfiguration()
config.websiteDataStore = .nonPersistent()
config.userContentController.add(printer, name: "done")
let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 794, height: 1123), styleMask: [.borderless], backing: .buffered, defer: false)
let view = WKWebView(frame: window.contentView!.bounds, configuration: config)
window.contentView = view
printer.view = view
printer.window = window
view.loadHTMLString(html, baseURL: nil)
// The server bounds the whole conversion; this only stops a stalled renderer.
DispatchQueue.main.asyncAfter(deadline: .now() + 300) { fail("render timed out", 5) }
app.run()
