import Foundation
import SwiftUI
import Combine

/// `UserDefaults`-backed app settings: the default save location used to
/// pre-fill the "Save all" sheet, and whether the queue of converted files
/// should be cleared on quit (vs. persisted and reloaded on next launch).
@MainActor
final class AppSettings: ObservableObject {
    private enum Keys {
        static let defaultSaveLocation = "defaultSaveLocationPath"
        static let clearQueueOnQuit = "clearQueueOnQuit"
    }

    /// Absolute path to the folder "Save all" and Settings pre-fill as the
    /// destination. Defaults to the user's Downloads folder.
    @Published var defaultSaveLocationPath: String {
        didSet {
            UserDefaults.standard.set(defaultSaveLocationPath, forKey: Keys.defaultSaveLocation)
        }
    }

    /// When true, the converted-file queue is discarded on quit. When
    /// false (the default), `.done` files are persisted to disk and
    /// reloaded on the next launch. A reasonable product default is to
    /// keep work between launches, so this defaults to `false`.
    @Published var clearQueueOnQuit: Bool {
        didSet {
            UserDefaults.standard.set(clearQueueOnQuit, forKey: Keys.clearQueueOnQuit)
        }
    }

    static let bundledExtrasCount = 8
    static let bundledExtrasDescription = "Bundled Python 3.12 · markitdown with \(bundledExtrasCount) extras"

    init() {
        let defaults = UserDefaults.standard
        if let saved = defaults.string(forKey: Keys.defaultSaveLocation), !saved.isEmpty {
            defaultSaveLocationPath = saved
        } else {
            let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            defaultSaveLocationPath = downloads?.path ?? NSHomeDirectory()
        }

        if defaults.object(forKey: Keys.clearQueueOnQuit) != nil {
            clearQueueOnQuit = defaults.bool(forKey: Keys.clearQueueOnQuit)
        } else {
            clearQueueOnQuit = false
        }
    }

    var defaultSaveLocationURL: URL {
        URL(fileURLWithPath: defaultSaveLocationPath, isDirectory: true)
    }
}
