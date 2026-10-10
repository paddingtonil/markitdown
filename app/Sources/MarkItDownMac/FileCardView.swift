import SwiftUI
import AppKit

/// A single card in the file stream. Renders one of four visual states
/// (converted/collapsed, converted/expanded, converting, failed) depending
/// on the file's `DroppedFile.Status`, matching the "Direction 1c" spec.
struct FileCardView: View {
    @EnvironmentObject private var viewModel: ConversionViewModel
    let file: DroppedFile

    private var isExpanded: Bool {
        viewModel.expandedID == file.id
    }

    private var isFailed: Bool {
        if case .failed = file.status { return true }
        return false
    }

    private var isNeedsReview: Bool {
        if case .needsReview = file.status { return true }
        return false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if isExpanded {
                switch file.status {
                case .done(let markdown, _), .needsReview(let markdown, _):
                    expandedPanel(markdown)
                default:
                    EmptyView()
                }
            }
        }
        .padding(.vertical, 14)
        .padding(.horizontal, 22)
        .background(
            RoundedRectangle(cornerRadius: DS.Radius.lg, style: .continuous)
                .fill(Color.white)
        )
        .overlay(
            RoundedRectangle(cornerRadius: DS.Radius.lg, style: .continuous)
                .strokeBorder((isFailed || isNeedsReview) ? DS.Color.accent300 : Color.clear, lineWidth: 1.5)
        )
        .shadow(color: Color.black.opacity(0.08), radius: 14, x: 0, y: 4)
        .contextMenu {
            if isFailed {
                Button("Retry") { viewModel.retry(file) }
            }
            if isNeedsReview {
                Button("Use Anyway") { viewModel.acceptReview(file) }
                Button("Retry") { viewModel.retry(file) }
            }
            Button("Remove", role: .destructive) { viewModel.remove(file) }
        }
    }

    // MARK: - Header row (present in every state)

    @ViewBuilder
    private var header: some View {
        HStack(spacing: 14) {
            leadingIcon

            titleAndMeta
                .contentShape(Rectangle())
                .onTapGesture {
                    viewModel.toggleExpanded(file)
                }

            Spacer(minLength: 8)

            trailing
        }
    }

    @ViewBuilder
    private var titleAndMeta: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(file.displayName)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(DS.Color.text)
                .lineLimit(1)
                .truncationMode(.middle)

            switch file.status {
            case .pending:
                Text("Waiting to convert…")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Color.neutral700)
            case .converting:
                IndeterminateBar()
                    .frame(height: 4)
                    .frame(maxWidth: 220)
            case .done(let markdown, let warnings):
                Text(DroppedFile.metaLine(fileTypeLabel: file.fileTypeLabel, markdown: markdown))
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Color.neutral700)
                if let firstWarning = warnings.first {
                    HStack(spacing: 4) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.system(size: 9))
                        Text(warnings.count > 1 ? "\(warnings.count) validation notes" : firstWarning)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(DS.Color.accent700)
                    .help(warnings.joined(separator: "\n\n"))
                }
            case .needsReview(let markdown, let warnings):
                Text(DroppedFile.metaLine(fileTypeLabel: file.fileTypeLabel, markdown: markdown))
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Color.neutral700)
                HStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 9))
                    Text(warnings.first ?? "This conversion needs review before use.")
                        .lineLimit(2)
                        .truncationMode(.tail)
                }
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(DS.Color.accent900)
                .help(warnings.joined(separator: "\n\n"))
            case .failed(let message):
                Text(message)
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Color.accent900)
                    .lineLimit(2)
            }
        }
    }

    // MARK: - Leading icon

    @ViewBuilder
    private var leadingIcon: some View {
        switch file.status {
        case .pending:
            circularIcon(background: DS.Color.accent100) {
                Image(systemName: "doc")
                    .foregroundStyle(DS.Color.accent600)
            }
        case .converting:
            circularIcon(background: DS.Color.accent100) {
                SpinningRing()
            }
        case .done(_, let warnings):
            circularIcon(background: DS.Color.accent100) {
                Image(systemName: warnings.isEmpty ? "doc.text" : "doc.text.magnifyingglass")
                    .foregroundStyle(DS.Color.accent600)
            }
        case .needsReview, .failed:
            circularIcon(background: DS.Color.accent200) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(DS.Color.accent900)
            }
        }
    }

    private func circularIcon<Content: View>(background: Color, @ViewBuilder content: () -> Content) -> some View {
        ZStack {
            Circle().fill(background)
            content()
        }
        .frame(width: 40, height: 40)
    }

    // MARK: - Trailing controls

    @ViewBuilder
    private var trailing: some View {
        switch file.status {
        case .pending:
            EmptyView()
        case .converting:
            PillTag(text: "Converting", background: DS.Color.accent100, foreground: DS.Color.accent700)
        case .done(let markdown, let warnings):
            HStack(spacing: 10) {
                if warnings.isEmpty {
                    PillTag(text: "Converted", background: DS.Color.accent2_100, foreground: DS.Color.accent2_700)
                } else {
                    PillTag(text: "Converted*", background: DS.Color.accent100, foreground: DS.Color.accent700)
                        .help(warnings.joined(separator: "\n\n"))
                }
                Button {
                    copyToPasteboard(markdown)
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .buttonStyle(CircularIconButtonStyle())
                .help("Copy Markdown")

                Button {
                    saveAs(markdown: markdown, suggestedName: file.suggestedMarkdownFilename)
                } label: {
                    Image(systemName: "square.and.arrow.down")
                }
                .buttonStyle(CircularIconButtonStyle())
                .help("Save As…")
            }
        case .needsReview(let markdown, let warnings):
            HStack(spacing: 10) {
                PillTag(text: "Needs review", background: DS.Color.accent200, foreground: DS.Color.accent900)
                    .help(warnings.joined(separator: "\n\n"))

                Button {
                    viewModel.retry(file)
                } label: {
                    Text("Retry")
                }
                .buttonStyle(.plain)
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(DS.Color.accent700)
                .padding(.horizontal, 12)
                .padding(.vertical, 5)
                .background(Capsule().fill(DS.Color.accent100))

                Button {
                    viewModel.acceptReview(file)
                } label: {
                    Text("Use Anyway")
                }
                .buttonStyle(.plain)
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(Color.white)
                .padding(.horizontal, 12)
                .padding(.vertical, 5)
                .background(Capsule().fill(DS.Color.accent900))
                .help("The converted text is available in the panel below -- copy or save it directly if you'd rather not mark it as reviewed yet.")

                Button {
                    copyToPasteboard(markdown)
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .buttonStyle(CircularIconButtonStyle())
                .help("Copy Markdown")
            }
        case .failed:
            Button {
                viewModel.retry(file)
            } label: {
                Text("Retry")
            }
            .buttonStyle(.plain)
            .font(.system(size: 12, weight: .bold))
            .foregroundStyle(DS.Color.accent700)
            .padding(.horizontal, 12)
            .padding(.vertical, 5)
            .background(Capsule().fill(DS.Color.accent100))
        }
    }

    // MARK: - Expanded markdown panel

    private func expandedPanel(_ markdown: String) -> some View {
        ScrollView {
            Text(markdown)
                .font(.system(size: 12.5, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
        }
        .frame(maxHeight: 360)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(DS.Color.neutral100)
        )
        .padding(.top, 14)
    }

    // MARK: - Actions

    private func copyToPasteboard(_ markdown: String) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(markdown, forType: .string)
    }

    private func saveAs(markdown: String, suggestedName: String) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedName
        panel.allowedContentTypes = [.text]
        if panel.runModal() == .OK, let url = panel.url {
            try? markdown.write(to: url, atomically: true, encoding: .utf8)
        }
    }
}

// MARK: - Converting-state decorations

/// A looping rotating arc used inside the leading icon while a file is
/// converting. SwiftUI-native (Circle trim + rotation animation) rather
/// than matching any specific CSS keyframe mechanics.
private struct SpinningRing: View {
    @State private var isSpinning = false

    var body: some View {
        ZStack {
            Circle()
                .stroke(DS.Color.accent200, lineWidth: 3)
            Circle()
                .trim(from: 0, to: 0.28)
                .stroke(DS.Color.accent, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                .rotationEffect(.degrees(isSpinning ? 360 : 0))
                .animation(.linear(duration: 0.9).repeatForever(autoreverses: false), value: isSpinning)
        }
        .frame(width: 22, height: 22)
        .onAppear { isSpinning = true }
    }
}

/// An indeterminate progress bar (a sliding highlight segment) for the
/// "converting" card state. Real conversion progress isn't granular, so
/// this loops rather than showing a fabricated fraction.
private struct IndeterminateBar: View {
    @State private var animate = false

    var body: some View {
        GeometryReader { proxy in
            let width = proxy.size.width
            let segmentWidth = max(width * 0.35, 24)

            ZStack(alignment: .leading) {
                Capsule().fill(DS.Color.neutral200)
                Capsule()
                    .fill(DS.Color.accent)
                    .frame(width: segmentWidth)
                    .offset(x: animate ? width - segmentWidth : 0)
                    .animation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true), value: animate)
            }
        }
        .clipShape(Capsule())
        .onAppear { animate = true }
    }
}
