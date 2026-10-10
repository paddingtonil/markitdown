import SwiftUI
import UniformTypeIdentifiers
import AppKit

@MainActor
final class ConversionViewModel: ObservableObject {
    @Published var files: [DroppedFile] = []
    @Published var expandedID: DroppedFile.ID?
    @Published var isDropTargeted: Bool = false
    @Published var serviceUnavailableMessage: String?
    @Published var showSaveAllSheet: Bool = false

    private var service: ConversionService?

    init() {
        do {
            service = try ConversionService()
        } catch {
            serviceUnavailableMessage = error.localizedDescription
        }
    }

    /// Restores any previously persisted `.done` files (see
    /// `QueuePersistence`), when "Clear the queue on quit" is off. Called
    /// once at launch; a no-op if the queue already has files.
    func loadPersistedQueue() {
        guard files.isEmpty else { return }
        let restored = QueuePersistence.load()
        guard !restored.isEmpty else { return }
        files.append(contentsOf: restored)
    }

    func addFiles(_ urls: [URL]) {
        for url in urls {
            let file = DroppedFile(url: url)
            files.append(file)
            convert(file)
        }
    }

    private func index(of id: DroppedFile.ID) -> Int? {
        files.firstIndex { $0.id == id }
    }

    private func convert(_ file: DroppedFile) {
        guard let service else {
            update(file.id, status: .failed(serviceUnavailableMessage ?? "Python runtime unavailable."))
            return
        }
        update(file.id, status: .converting)
        Task {
            do {
                let output = try await service.convert(fileURL: file.url)
                if output.needsConfirmation {
                    update(file.id, status: .needsReview(markdown: output.markdown, warnings: output.warnings))
                } else {
                    update(file.id, status: .done(markdown: output.markdown, warnings: output.warnings))
                }
            } catch {
                update(file.id, status: .failed(error.localizedDescription))
            }
        }
    }

    private func update(_ id: DroppedFile.ID, status: DroppedFile.Status) {
        guard let idx = index(of: id) else { return }
        files[idx].status = status
    }

    func retry(_ file: DroppedFile) {
        convert(file)
    }

    /// The user has reviewed a `.needsReview` file's content and wants to
    /// treat it as a normal conversion (make it eligible for "Save all",
    /// have it persist across launches, etc.) despite the validation
    /// warning. Promotes it to `.done` in place.
    func acceptReview(_ file: DroppedFile) {
        guard case .needsReview(let markdown, let warnings) = file.status else { return }
        update(file.id, status: .done(markdown: markdown, warnings: warnings))
    }

    func remove(_ file: DroppedFile) {
        files.removeAll { $0.id == file.id }
        if expandedID == file.id {
            expandedID = nil
        }
    }

    /// Tapping a collapsed converted card expands it in place; tapping the
    /// open one collapses it back. Only one card is expanded at a time.
    /// Works for `.needsReview` too, since the user needs to see the
    /// content to decide whether to accept it.
    func toggleExpanded(_ file: DroppedFile) {
        switch file.status {
        case .done, .needsReview:
            expandedID = (expandedID == file.id) ? nil : file.id
        default:
            return
        }
    }

    var doneFiles: [DroppedFile] {
        files.filter {
            if case .done = $0.status { return true }
            return false
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var viewModel: ConversionViewModel
    @EnvironmentObject private var settings: AppSettings

    var body: some View {
        ZStack {
            DS.Color.bg

            VStack(spacing: 0) {
                if let message = viewModel.serviceUnavailableMessage {
                    Text(message)
                        .font(.system(size: 12))
                        .foregroundStyle(.white)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(DS.Color.accent900)
                }

                Group {
                    if viewModel.files.isEmpty {
                        EmptyStateView(onChooseFile: chooseFile)
                    } else {
                        FileStreamView(onChooseFile: chooseFile)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            // Whole-window drop feedback: a subtle accent-tinted border/
            // overlay while a drag is over the window. The design doesn't
            // specify an exact hover treatment, so this is a light,
            // unobtrusive cue rather than a dedicated drop-zone rectangle.
            if viewModel.isDropTargeted {
                RoundedRectangle(cornerRadius: 0)
                    .fill(DS.Color.accent.opacity(0.05))
                    .overlay(
                        RoundedRectangle(cornerRadius: 0)
                            .strokeBorder(DS.Color.accent, lineWidth: 3)
                    )
                    .allowsHitTesting(false)
                    .ignoresSafeArea()
            }
        }
        .frame(minWidth: 760, minHeight: 560)
        // The whole window is the drop target, per the design principle
        // ("no dedicated drop zone rectangle") — attached at the root,
        // not on a small sub-view.
        .onDrop(of: [UTType.fileURL], isTargeted: $viewModel.isDropTargeted) { providers in
            handleDrop(providers: providers)
        }
        .toolbarBackground(DS.Color.neutral200, for: .windowToolbar)
        .toolbarBackground(.visible, for: .windowToolbar)
        .toolbar {
            ToolbarItem(placement: .navigation) {
                if !viewModel.files.isEmpty {
                    Text("\(viewModel.files.count) file\(viewModel.files.count == 1 ? "" : "s")")
                        .font(.caprasimo(16))
                        .foregroundStyle(DS.Color.text)
                }
            }
            if !viewModel.files.isEmpty {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        viewModel.showSaveAllSheet = true
                    } label: {
                        Label("Save all", systemImage: "square.and.arrow.down")
                    }
                    .buttonStyle(PrimaryPillButtonStyle())
                }
            }
            ToolbarItem(placement: .primaryAction) {
                Button {
                    openSettings()
                } label: {
                    Image(systemName: "gearshape")
                        .foregroundStyle(DS.Color.neutral700)
                }
                .buttonStyle(.plain)
                .help("Settings")
            }
        }
        .sheet(isPresented: $viewModel.showSaveAllSheet) {
            SaveAllSheet()
        }
    }

    private func handleDrop(providers: [NSItemProvider]) -> Bool {
        guard !providers.isEmpty else { return false }
        for provider in providers {
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url else { return }
                DispatchQueue.main.async {
                    viewModel.addFiles([url])
                }
            }
        }
        return true
    }

    private func chooseFile() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.prompt = "Convert"
        if panel.runModal() == .OK {
            viewModel.addFiles(panel.urls)
        }
    }

    /// Opens the app's Settings scene. `SettingsLink` requires macOS 14+
    /// and this project targets macOS 13, so this triggers the same
    /// target/action pair already wired up on the app menu's "Settings…"
    /// item (which SwiftUI's `Settings { }` scene installs automatically) —
    /// more robust than guessing the private `showSettingsWindow:`
    /// selector directly, which was unreliable in testing.
    private func openSettings() {
        NSApp.activate(ignoringOtherApps: true)
        if let settingsItem = NSApp.mainMenu?.items.first?.submenu?.items.first(where: {
            $0.title.contains("Settings") || $0.title.contains("Preferences")
        }), let action = settingsItem.action {
            NSApp.sendAction(action, to: settingsItem.target, from: settingsItem)
        } else {
            // Fallback for the unlikely case the menu item can't be found.
            NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
        }
    }
}

/// The file-queue state: a scrollable single column of cards, generous
/// padding, plus a "drop more files" strip reinforcing that the whole
/// window (not just this strip) accepts drops.
private struct FileStreamView: View {
    @EnvironmentObject private var viewModel: ConversionViewModel
    let onChooseFile: () -> Void

    var body: some View {
        GeometryReader { proxy in
            let horizontalPadding = min(max(proxy.size.width * 0.08, 24), 64)

            ScrollView {
                VStack(spacing: 16) {
                    ForEach(viewModel.files) { file in
                        FileCardView(file: file)
                    }

                    dropMoreStrip
                }
                .padding(.horizontal, horizontalPadding)
                .padding(.top, 28)
                .padding(.bottom, 40)
            }
        }
    }

    private var dropMoreStrip: some View {
        Text("+ Drop more files anywhere")
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(DS.Color.accent700)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(
                Capsule()
                    .strokeBorder(DS.Color.neutral300, style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
            )
            .padding(.top, 4)
    }
}
