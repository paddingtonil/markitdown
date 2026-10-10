import Foundation

/// Result of a successful conversion: the Markdown itself, plus any
/// post-conversion validation warnings `convert_driver.py` attached (e.g.
/// visual content that couldn't be converted to text). Decoded directly
/// from the driver's JSON stdout.
struct ConversionOutput: Decodable {
    let markdown: String
    let warnings: [String]
    /// True when `convert_driver.py`'s validation pass judged the content
    /// unreliable enough (currently: a Hebrew orthography-violation rate far
    /// above this project's known-good baselines, with no automatic
    /// correction available) that the result shouldn't be treated as a
    /// normal successful conversion without the user confirming they still
    /// want it -- see `_validate` in convert_driver.py.
    let needsConfirmation: Bool

    private enum CodingKeys: String, CodingKey {
        case markdown, warnings
        case needsConfirmation = "needs_confirmation"
    }
}

enum ConversionError: LocalizedError {
    case pythonRuntimeNotFound
    case driverScriptNotFound
    case processFailed(exitCode: Int32, stderr: String)
    case launchFailed(String)
    case malformedOutput(String)

    var errorDescription: String? {
        switch self {
        case .pythonRuntimeNotFound:
            return "Could not locate the bundled Python runtime (python-runtime/bin/python3)."
        case .driverScriptNotFound:
            return "Could not locate convert_driver.py."
        case .processFailed(let exitCode, let stderr):
            let trimmed = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                return "Conversion failed (exit code \(exitCode))."
            }
            return "Conversion failed (exit code \(exitCode)):\n\(trimmed)"
        case .launchFailed(let message):
            return "Could not launch the conversion process: \(message)"
        case .malformedOutput(let message):
            return "Conversion process produced unreadable output: \(message)"
        }
    }
}

/// Runs the bundled Python interpreter + `convert_driver.py` as a subprocess
/// to convert a file to Markdown via the vendored `markitdown` package.
final class ConversionService {

    /// Absolute path to the bundled `python3` executable.
    private let pythonPath: URL
    /// Absolute path to `convert_driver.py`.
    private let driverPath: URL

    init() throws {
        let (python, driver) = try Self.locateRuntime()
        self.pythonPath = python
        self.driverPath = driver
    }

    /// Locates the python runtime and driver script, first assuming we are
    /// running from inside a packaged `.app` bundle, then falling back to a
    /// development layout (running via `swift run` from the source tree).
    private static func locateRuntime() throws -> (python: URL, driver: URL) {
        let fileManager = FileManager.default

        // 1. Packaged app: Contents/Resources/python-runtime/bin/python3
        //                  Contents/Resources/convert_driver.py
        if let resourceURL = Bundle.main.resourceURL {
            let python = resourceURL
                .appendingPathComponent("python-runtime")
                .appendingPathComponent("bin")
                .appendingPathComponent("python3")
            let driver = resourceURL.appendingPathComponent("convert_driver.py")
            if fileManager.isExecutableFile(atPath: python.path),
               fileManager.fileExists(atPath: driver.path) {
                return (python, driver)
            }
        }

        // 2. Development layout: walk up from this source file to the
        //    project root, then look under build/python-runtime and engine/.
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidatePython = dir
                .appendingPathComponent("build")
                .appendingPathComponent("python-runtime")
                .appendingPathComponent("bin")
                .appendingPathComponent("python3")
            let candidateDriver = dir
                .appendingPathComponent("engine")
                .appendingPathComponent("convert_driver.py")
            if fileManager.isExecutableFile(atPath: candidatePython.path),
               fileManager.fileExists(atPath: candidateDriver.path) {
                return (candidatePython, candidateDriver)
            }
            dir = dir.deletingLastPathComponent()
        }

        // Distinguish which piece is missing for a clearer error, preferring
        // the project-root-relative dev layout for the final message.
        throw ConversionError.pythonRuntimeNotFound
    }

    /// Converts the file at `fileURL` to Markdown, running the bundled
    /// Python + markitdown as a subprocess. The driver validates its own
    /// output (e.g. flags visual content that couldn't be converted to
    /// text) and reports that alongside the Markdown; see
    /// `ConversionOutput`. Throws `ConversionError` with the captured
    /// stderr on failure.
    func convert(fileURL: URL) async throws -> ConversionOutput {
        try await withCheckedThrowingContinuation { continuation in
            let process = Process()
            process.executableURL = pythonPath
            process.arguments = [driverPath.path, fileURL.path]

            let stdoutPipe = Pipe()
            let stderrPipe = Pipe()
            process.standardOutput = stdoutPipe
            process.standardError = stderrPipe

            let stdoutData = SynchronizedData()
            let stderrData = SynchronizedData()

            stdoutPipe.fileHandleForReading.readabilityHandler = { handle in
                let data = handle.availableData
                if !data.isEmpty {
                    stdoutData.append(data)
                }
            }
            stderrPipe.fileHandleForReading.readabilityHandler = { handle in
                let data = handle.availableData
                if !data.isEmpty {
                    stderrData.append(data)
                }
            }

            process.terminationHandler = { proc in
                stdoutPipe.fileHandleForReading.readabilityHandler = nil
                stderrPipe.fileHandleForReading.readabilityHandler = nil

                let stderrString = String(data: stderrData.value, encoding: .utf8) ?? ""

                if proc.terminationStatus == 0 {
                    do {
                        let output = try JSONDecoder().decode(ConversionOutput.self, from: stdoutData.value)
                        continuation.resume(returning: output)
                    } catch {
                        continuation.resume(throwing: ConversionError.malformedOutput(error.localizedDescription))
                    }
                } else {
                    continuation.resume(throwing: ConversionError.processFailed(
                        exitCode: proc.terminationStatus,
                        stderr: stderrString
                    ))
                }
            }

            do {
                try process.run()
            } catch {
                continuation.resume(throwing: ConversionError.launchFailed(error.localizedDescription))
            }
        }
    }
}

/// Small thread-safe byte buffer used to accumulate pipe output from the
/// readabilityHandler callbacks, which fire on a background queue.
private final class SynchronizedData: @unchecked Sendable {
    private var data = Data()
    private let lock = NSLock()

    func append(_ chunk: Data) {
        lock.lock()
        data.append(chunk)
        lock.unlock()
    }

    var value: Data {
        lock.lock()
        defer { lock.unlock() }
        return data
    }
}
