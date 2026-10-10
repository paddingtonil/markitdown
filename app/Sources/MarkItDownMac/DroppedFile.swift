import Foundation

/// A single file that has been dropped (or chosen) for conversion, and its
/// current place in the conversion pipeline.
struct DroppedFile: Identifiable, Equatable {
    enum Status: Equatable {
        case pending
        case converting
        case done(markdown: String, warnings: [String])
        /// Conversion succeeded, but the driver's validation pass judged
        /// the content unreliable enough (see `ConversionOutput.needsConfirmation`)
        /// that it shouldn't be treated as a normal successful conversion --
        /// the user must explicitly accept it (see `ConversionViewModel.acceptReview`)
        /// before it counts toward "Save all" or gets persisted across launches.
        case needsReview(markdown: String, warnings: [String])
        case failed(String)    // error message (stderr, or a description)
    }

    let id: UUID
    let url: URL
    var status: Status

    init(url: URL, status: Status = .pending) {
        self.id = UUID()
        self.url = url
        self.status = status
    }

    var displayName: String {
        url.lastPathComponent
    }

    /// Suggested filename for "Save As…": <original-basename>.md
    var suggestedMarkdownFilename: String {
        url.deletingPathExtension().lastPathComponent + ".md"
    }

    /// Uppercased file extension, used as a lightweight "file type" label
    /// on the card (e.g. "PDF", "DOCX") since we don't have real
    /// page/slide counts available from the conversion pipeline.
    var fileTypeLabel: String {
        let ext = url.pathExtension
        return ext.isEmpty ? "FILE" : ext.uppercased()
    }

    /// A short, honest meta line for a converted card. We don't have
    /// slide/page counts from the conversion pipeline, so this uses the
    /// file type plus the converted Markdown's character count rather
    /// than fabricating precision we don't have.
    static func metaLine(fileTypeLabel: String, markdown: String) -> String {
        let count = markdown.count
        let formatted = NumberFormatter.localizedString(from: NSNumber(value: count), number: .decimal)
        return "\(fileTypeLabel) · \(formatted) character\(count == 1 ? "" : "s")"
    }
}
