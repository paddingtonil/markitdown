import SwiftUI

/// First-launch / no-files state: the big Caprasimo headline, body copy,
/// action buttons, and a bundled-runtime status row, with two soft
/// decorative circles in the background on the right.
struct EmptyStateView: View {
    let onChooseFile: () -> Void
    @State private var showsFormatsPopover = false

    var body: some View {
        ZStack(alignment: .topLeading) {
            decorativeCircles

            content
                .frame(maxWidth: 620, alignment: .leading)
                .padding(.leading, 64)
                .padding(.top, 96)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 22) {
            Text("Anything in.\nMarkdown out.")
                .font(.caprasimo(64))
                .foregroundStyle(DS.Color.text)
                .lineSpacing(0)
                .fixedSize(horizontal: false, vertical: true)

            Text("Drop a file anywhere in this window. PDFs, Word, PowerPoint, Excel, Outlook messages, HTML, CSV, EPUB, notebooks and ZIP archives all convert on your machine — nothing is uploaded.")
                .font(.system(size: 17))
                .foregroundStyle(DS.Color.neutral700)
                .frame(maxWidth: 460, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 12) {
                Button {
                    onChooseFile()
                } label: {
                    Label("Choose a file", systemImage: "arrow.up.doc")
                }
                .buttonStyle(PrimaryPillButtonStyle())

                Button {
                    showsFormatsPopover = true
                } label: {
                    Text("See supported formats")
                }
                .buttonStyle(SecondaryPillButtonStyle())
                .popover(isPresented: $showsFormatsPopover, arrowEdge: .bottom) {
                    SupportedFormatsPopover()
                }
            }
            .padding(.top, 4)

            HStack(spacing: 8) {
                Circle()
                    .fill(DS.Color.accent2)
                    .frame(width: 8, height: 8)
                Text("Bundled Python 3.12 runtime ready · works offline")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Color.neutral700)
            }
            .padding(.top, 6)
        }
    }

    private var decorativeCircles: some View {
        GeometryReader { proxy in
            ZStack {
                Circle()
                    .fill(DS.Color.neutral200)
                    .frame(width: 340, height: 340)
                    .position(x: proxy.size.width - 60, y: 140)

                Circle()
                    .fill(DS.Color.accent2_200)
                    .frame(width: 200, height: 200)
                    .position(x: proxy.size.width - 40, y: 360)
            }
        }
        .allowsHitTesting(false)
    }
}

/// Lightweight popover listing the formats supported by the bundled
/// runtime (mirrors README.md's "Supported formats" section). Kept
/// intentionally simple per the design spec — this isn't the focus.
private struct SupportedFormatsPopover: View {
    private let formats: [String] = [
        "Plain text, Markdown, HTML, CSV, JSON, RSS/Atom feeds",
        "Jupyter notebooks (.ipynb), EPUB, ZIP archives",
        "PDF (.pdf)",
        "Word (.docx), PowerPoint (.pptx), Excel (.xlsx, .xls)",
        "Outlook messages (.msg)",
        "Images — metadata/EXIF extraction only"
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Supported formats")
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(DS.Color.text)

            ForEach(formats, id: \.self) { line in
                HStack(alignment: .top, spacing: 8) {
                    Circle()
                        .fill(DS.Color.accent)
                        .frame(width: 5, height: 5)
                        .padding(.top, 6)
                    Text(line)
                        .font(.system(size: 12.5))
                        .foregroundStyle(DS.Color.neutral700)
                }
            }
        }
        .padding(16)
        .frame(width: 320, alignment: .leading)
        .background(DS.Color.bg)
    }
}
