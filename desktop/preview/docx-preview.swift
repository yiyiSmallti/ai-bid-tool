// bid-docx-preview renders a DOCX to an A4 PDF for export page previews:
// docx-preview lays the document out as HTML in WebKit, which prints it to pages.
// Printing waits for fonts and a short pause: a system font loaded on first use, such
// as 黑体 in a heading, otherwise leaves every page blank.
// Usage: bid-docx-preview IN.docx OUT.pdf. The scripts sit next to the binary.
// bid-docx-preview --html IN.html OUT.png draws one page of a generated system
// screen instead (see Screen below).
// Exit 0 writes the output; any failure prints one line to stderr and exits non-zero.
import AppKit
import WebKit

let args = CommandLine.arguments
func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}
if args.count == 4 && args[1] == "--html" { Screen.run(input: args[2], output: args[3]) }
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
// The page places every printed page break itself, because each break WebKit
// makes shifts the pages after it: it pushes a line that crosses the page end to
// the next page, drops the top margin of a block it moves there, and cuts table
// rows. A spacer before a block reaches the page end instead, as a page break
// (break-inside or break-before) would clip the moved block.
// docx-preview drops keep-with-next, so each run of such paragraphs is wrapped
// with the block after it, and a group that would cross a printed page moves
// down to the next one, as Word keeps them together. A paragraph that crosses a
// page is split before the crossing line, and the rest starts the next page.
// A table is split at the last whole row that fits: the rest becomes a new table
// on the next page that opens with the header rows (w:tblHeader), so no row is
// cut and each page's part is closed.
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
// A printed page can start a few pixels away from where the page counts it, so a
// table part leaves room at the page end and starts a little below the page
// start, where its top border cannot show at the bottom of the page before.
const slack = 24;
const edge = 16;
function split(table, origin) {
  for (let part = table, tries = table.rows.length; tries > 0; tries--) {
    const rows = [...part.rows];
    const header = rows.findIndex(row => !row.dataset.header);
    if (header < 0) return;
    const top = part.getBoundingClientRect().top;
    const end = pageEnd(origin, top);
    // A table that starts a page on its own starts at `edge` too.
    if (top - (end - height) < edge - 1) {
      space(part, end - height + edge - top);
      continue;
    }
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
// The paragraph from the line that crosses `end` on becomes a new paragraph that
// starts the next page, so print never moves a line itself; returns it, or nothing
// when the whole paragraph moved.
function broken(paragraph, end) {
  const characters = [];
  const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode())
    for (let offset = 0; offset < node.length; offset++) characters.push([node, offset]);
  const box = character => {
    const range = document.createRange();
    range.setStart(...character);
    range.setEnd(character[0], character[1] + 1);
    return range.getBoundingClientRect();
  };
  const crossing = characters.find(character => { const rect = box(character); return rect.height && rect.bottom > end; });
  const line = crossing && box(crossing).top;
  const at = crossing ? characters.findIndex(character => { const rect = box(character); return rect.height && rect.bottom > line + 1; }) : 0;
  if (at <= 0) {
    space(paragraph, end - paragraph.getBoundingClientRect().top);
    return null;
  }
  const range = document.createRange();
  range.setStart(...characters[at]);
  range.setEndAfter(paragraph.lastChild);
  const rest = paragraph.cloneNode(false);
  rest.append(range.extractContents());
  rest.style.textIndent = "0";
  rest.style.marginTop = "0";
  paragraph.style.marginBottom = "0";
  if (getComputedStyle(paragraph).textAlign === "justify") paragraph.style.textAlignLast = "justify";
  paragraph.after(rest);
  space(rest, end - rest.getBoundingClientRect().top);
  return rest;
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
    const blocks = [...document.querySelectorAll("section.docx, article > *")];
    for (let index = 0; index < blocks.length; index++) {
      const block = blocks[index];
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
      const end = pageEnd(origin, box.top);
      const table = block.tagName === "TABLE" ? block
        : block.className === "keep" && block.lastElementChild?.tagName === "TABLE"
          ? block.lastElementChild : null;
      if (block.className === "keep" && box.bottom > end && box.height < height)
        space(block, end - box.top);
      else if (table) split(table, origin);
      else if (block.tagName === "P" && box.bottom > end) {
        const rest = broken(block, end);
        if (rest) blocks.splice(index + 1, 0, rest);
      } else if (box.bottom > end && box.height < height) space(block, end - box.top);
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

// A model-written page of the proposed system, drawn at a 1440-point desktop width
// and the height of its content. The page is untrusted: its scripts never run, and
// the CSP put ahead of its own markup blocks every load but inline styles and data
// images, so the image shows only what the HTML itself holds.
final class Screen: NSObject, WKNavigationDelegate {
    static let width: CGFloat = 1440
    static let policy = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:\">"
    var view: WKWebView!
    let output: URL

    init(output: URL) { self.output = output }

    static func run(input: String, output: String) -> Never {
        guard let page = try? String(contentsOf: URL(fileURLWithPath: input), encoding: .utf8) else {
            fail("cannot read input", 2)
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        let screen = Screen(output: URL(fileURLWithPath: output))
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: 900), styleMask: [.borderless], backing: .buffered, defer: false)
        let view = WKWebView(frame: window.contentView!.bounds, configuration: config)
        window.contentView = view
        view.navigationDelegate = screen
        screen.view = view
        // After the doctype, so the page keeps standards mode; the parser moves the
        // policy into the page's head ahead of anything the page itself declares.
        var html = policy + page
        if page.lowercased().hasPrefix("<!doctype"), let end = page.firstIndex(of: ">") {
            html = String(page[...end]) + policy + String(page[page.index(after: end)...])
        }
        view.loadHTMLString(html, baseURL: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { fail("render timed out", 5) }
        app.run()
        exit(0)
    }

    // Only the page itself loads; a link or form in it never navigates away.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(action.request.url?.absoluteString == "about:blank" ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // System fonts such as PingFang load on first use and change line heights.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            webView.evaluateJavaScript("Math.ceil(document.documentElement.scrollHeight)") { value, _ in
                let height = min(max(CGFloat((value as? NSNumber)?.doubleValue ?? 900), 600), 2400)
                webView.window?.setContentSize(NSSize(width: Screen.width, height: height))
                webView.frame = NSRect(x: 0, y: 0, width: Screen.width, height: height)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.snapshot(height) }
            }
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        fail("render failed: \(error.localizedDescription)", 3)
    }

    func snapshot(_ height: CGFloat) {
        let config = WKSnapshotConfiguration()
        config.rect = NSRect(x: 0, y: 0, width: Screen.width, height: height)
        config.snapshotWidth = NSNumber(value: Double(Screen.width))
        view.takeSnapshot(with: config) { image, error in
            guard let image, let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
                  let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]) else {
                fail("snapshot failed: \(error?.localizedDescription ?? "no image")", 4)
            }
            do { try png.write(to: self.output) } catch { fail("cannot write output", 4) }
            exit(0)
        }
    }
}
