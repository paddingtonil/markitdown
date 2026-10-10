import SwiftUI
import AppKit

/// The "Save all" sheet: pick a destination folder and write every
/// `.done` file's Markdown to `<destination>/<basename>.md`, matching the
/// naming used by the existing per-file Save As.
struct SaveAllSheet: View {
    @EnvironmentObject private var viewModel: ConversionViewModel
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss

    @State private var destinationPath: String = ""

    private var doneFiles: [DroppedFile] {
        viewModel.doneFiles
    }

    private var pendingOrFailedFiles: [DroppedFile] {
        viewModel.files.filter {
            switch $0.status {
            case .done: return false
            default: return true
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Save \(doneFiles.count) Markdown file\(doneFiles.count == 1 ? "" : "s")")
                .font(.caprasimo(22))
                .foregroundStyle(DS.Color.text)

            pathRow

            summary

            Spacer(minLength: 0)

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .buttonStyle(SecondaryPillButtonStyle())
                Button("Save") { save() }
                    .buttonStyle(PrimaryPillButtonStyle())
                    .disabled(doneFiles.isEmpty)
            }
        }
        .padding(24)
        .frame(width: 480, height: 340)
        .background(Color.white)
        .onAppear {
            destinationPath = settings.defaultSaveLocationPath
        }
    }

    private var pathRow: some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "folder.fill")
                    .foregroundStyle(DS.Color.accent)
                Text(destinationPath)
                    .font(.system(size: 13))
                    .foregroundStyle(DS.Color.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 9)
            .background(Capsule().fill(DS.Color.neutral100))

            Button("Choose…") { chooseDestination() }
                .buttonStyle(SecondaryPillButtonStyle())
        }
    }

    @ViewBuilder
    private var summary: some View {
        VStack(alignment: .leading, spacing: 6) {
            if doneFiles.isEmpty {
                Text("No converted files are ready to save yet.")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Color.neutral700)
            } else {
                Text("Will save:")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(DS.Color.neutral700)
                ForEach(doneFiles) { file in
                    Text("• \(file.suggestedMarkdownFilename)")
                        .font(.system(size: 12))
                        .foregroundStyle(DS.Color.neutral700)
                }
            }

            ForEach(pendingOrFailedFiles) { file in
                Text(skipReasonText(for: file))
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Color.accent700)
            }
        }
    }

    private func skipReasonText(for file: DroppedFile) -> String {
        switch file.status {
        case .converting, .pending:
            return "\(file.displayName) is still converting and will be skipped."
        case .failed:
            return "\(file.displayName) failed to convert and will be skipped."
        case .needsReview:
            return "\(file.displayName) needs review before it can be saved -- see its card."
        case .done:
            return ""
        }
    }

    private func chooseDestination() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        panel.directoryURL = URL(fileURLWithPath: destinationPath, isDirectory: true)
        if panel.runModal() == .OK, let url = panel.url {
            destinationPath = url.path
        }
    }

    private func save() {
        let destination = URL(fileURLWithPath: destinationPath, isDirectory: true)
        for file in doneFiles {
            guard case .done(let markdown, _) = file.status else { continue }
            let target = destination.appendingPathComponent(file.suggestedMarkdownFilename)
            try? markdown.write(to: target, atomically: true, encoding: .utf8)
        }
        dismiss()
    }
}
