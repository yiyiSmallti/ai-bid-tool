// bid-docx-preview renders a DOCX to an A4 PDF for export page previews:
// docx-preview lays the document out as HTML in WebKit, which prints it to pages.
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
// bottom padding and every printed page keeps the same space.
let html = """
<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data: blob:">
<style>
body { margin: 0; background: white }
section.docx { min-height: 0 !important; height: auto !important; padding-top: 0 !important;
  padding-bottom: 0 !important; margin: 0 !important; box-shadow: none !important }
</style>
<script>\(script("jszip.min.js"))</script><script>\(script("docx-preview.min.js"))</script>
</head><body><div id="out"></div><script>
const bytes = Uint8Array.from(atob("\(docx.base64EncodedString())"), c => c.charCodeAt(0));
docx.renderAsync(bytes, document.getElementById("out"), null,
    {inWrapper: false, breakPages: true, ignoreLastRenderedPageBreak: true, useBase64URL: true})
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
