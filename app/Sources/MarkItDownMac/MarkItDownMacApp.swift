import SwiftUI
import AppKit

/// Handles queue persistence around app termination: when "Clear the queue
/// on quit" is off, the current `.done` files are written to disk so they
/// can be restored on the next launch; when it's on, any previously
/// persisted snapshot is discarded.
final class AppDelegate: NSObject, NSApplicationDelegate {
    var viewModel: ConversionViewModel?
    var settings: AppSettings?

    func applicationWillTerminate(_ notification: Notification) {
        guard let viewModel, let settings else { return }
        if settings.clearQueueOnQuit {
            QueuePersistence.clear()
        } else {
            QueuePersistence.save(viewModel.files)
        }
    }
}

@main
struct MarkItDownMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var settings = AppSettings()
    @StateObject private var viewModel = ConversionViewModel()

    init() {
        DS.registerFonts()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(viewModel)
                .environmentObject(settings)
                .frame(minWidth: 760, minHeight: 560)
                .onAppear {
                    appDelegate.viewModel = viewModel
                    appDelegate.settings = settings
                    if !settings.clearQueueOnQuit {
                        viewModel.loadPersistedQueue()
                    }
                }
        }
        .windowResizability(.contentSize)
        .windowToolbarStyle(.unified(showsTitle: false))

        Settings {
            SettingsView()
                .environmentObject(settings)
        }
    }
}
