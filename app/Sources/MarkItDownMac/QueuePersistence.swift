import Foundation

/// Persists the *converted* (`.done`) portion of the file queue to a small
/// JSON file, so it can be restored on next launch when the user has
/// "Clear the queue on quit" turned off. Pending/converting/failed entries
/// are not meaningful to resume and are skipped.
enum QueuePersistence {

    private struct PersistedFile: Codable {
        let path: String
        let markdown: String
        let warnings: [String]
    }

    private static let bundleID = Bundle.main.bundleIdentifier ?? "com.local.markitdown"

    private static var supportDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory() + "/Library/Application Support")
        return base.appendingPathComponent(bundleID, isDirectory: true)
    }

    private static var queueFileURL: URL {
        supportDirectory.appendingPathComponent("queue.json")
    }

    /// Saves the `.done` files from `files` to disk, overwriting any
    /// previous snapshot. Silently no-ops on failure (this is best-effort
    /// convenience state, not critical data).
    static func save(_ files: [DroppedFile]) {
        let persisted = files.compactMap { file -> PersistedFile? in
            guard case .done(let markdown, let warnings) = file.status else { return nil }
            return PersistedFile(path: file.url.path, markdown: markdown, warnings: warnings)
        }

        do {
            try FileManager.default.createDirectory(at: supportDirectory, withIntermediateDirectories: true)
            if persisted.isEmpty {
                try? FileManager.default.removeItem(at: queueFileURL)
                return
            }
            let data = try JSONEncoder().encode(persisted)
            try data.write(to: queueFileURL, options: .atomic)
        } catch {
            // Best-effort persistence; failures here shouldn't disrupt the app.
        }
    }

    /// Loads any previously persisted `.done` files. Files whose original
    /// path no longer exists on disk are skipped.
    static func load() -> [DroppedFile] {
        guard let data = try? Data(contentsOf: queueFileURL) else { return [] }
        guard let persisted = try? JSONDecoder().decode([PersistedFile].self, from: data) else { return [] }

        return persisted.compactMap { entry in
            guard FileManager.default.fileExists(atPath: entry.path) else { return nil }
            let url = URL(fileURLWithPath: entry.path)
            return DroppedFile(url: url, status: .done(markdown: entry.markdown, warnings: entry.warnings))
        }
    }

    /// Deletes any persisted snapshot (used when the user has "Clear the
    /// queue on quit" turned on).
    static func clear() {
        try? FileManager.default.removeItem(at: queueFileURL)
    }
}
