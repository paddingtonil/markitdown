import SwiftUI
import CoreText
import AppKit
import os.log

/// Design tokens for the "Direction 1c: Single Stream" visual redesign.
/// Named tokens only — the rest of the UI should never reference raw hex
/// values directly.
enum DS {

    // MARK: - Colors

    enum Color {
        static let bg = SwiftUI.Color(hex: 0xF5EAD8)
        static let surface = SwiftUI.Color(hex: 0xEBDDC5)
        static let text = SwiftUI.Color(hex: 0x201E1D)
        static let accent = SwiftUI.Color(hex: 0xC67139)
        static let accent2 = SwiftUI.Color(hex: 0x7A8A5E)
        static let divider = SwiftUI.Color(hex: 0x201E1D).opacity(0.16)

        // Neutrals
        static let neutral100 = SwiftUI.Color(hex: 0xF9F4ED)
        static let neutral200 = SwiftUI.Color(hex: 0xEEE7DB)
        static let neutral300 = SwiftUI.Color(hex: 0xDCD3C4)
        static let neutral400 = SwiftUI.Color(hex: 0xC0B6A5)
        static let neutral500 = SwiftUI.Color(hex: 0xA19786)
        static let neutral600 = SwiftUI.Color(hex: 0x82796A)
        static let neutral700 = SwiftUI.Color(hex: 0x645C50)
        static let neutral800 = SwiftUI.Color(hex: 0x474238)
        static let neutral900 = SwiftUI.Color(hex: 0x2E2B25)

        // Accent (terracotta) ramp
        static let accent100 = SwiftUI.Color(hex: 0xFFF2EB)
        static let accent200 = SwiftUI.Color(hex: 0xFFE1D0)
        static let accent300 = SwiftUI.Color(hex: 0xFFC6A5)
        static let accent400 = SwiftUI.Color(hex: 0xF6A06B)
        static let accent500 = SwiftUI.Color(hex: 0xD67F48)
        static let accent600 = SwiftUI.Color(hex: 0xB2622D)
        static let accent700 = SwiftUI.Color(hex: 0x8C491A)
        static let accent800 = SwiftUI.Color(hex: 0x643312)
        static let accent900 = SwiftUI.Color(hex: 0x402310)

        // Accent-2 (sage) ramp
        static let accent2_100 = SwiftUI.Color(hex: 0xF0FAE1)
        static let accent2_200 = SwiftUI.Color(hex: 0xE1EECC)
        static let accent2_300 = SwiftUI.Color(hex: 0xCCDBB2)
        static let accent2_400 = SwiftUI.Color(hex: 0xAEBF92)
        static let accent2_500 = SwiftUI.Color(hex: 0x8FA073)
        static let accent2_600 = SwiftUI.Color(hex: 0x728157)
        static let accent2_700 = SwiftUI.Color(hex: 0x56633F)
        static let accent2_800 = SwiftUI.Color(hex: 0x3D472B)
        static let accent2_900 = SwiftUI.Color(hex: 0x272E1B)
    }

    // MARK: - Radius

    enum Radius {
        static let sm: CGFloat = 8
        static let md: CGFloat = 16
        static let lg: CGFloat = 28
    }

    // MARK: - Font registration

    /// PostScript/family name of the registered display face. "Caprasimo"
    /// is a single-weight family, confirmed via `Font.custom` fallback
    /// behavior (falls back silently to the system font if this name isn't
    /// actually registered, so getting this string right matters).
    static let displayFontName = "Caprasimo"

    private static let registrationLog = Logger(subsystem: "com.local.markitdown", category: "font-registration")

    /// Whether `CTFontManagerRegisterFontsForURL` reported success for the
    /// bundled Caprasimo font. Checked at launch so a silent registration
    /// failure doesn't quietly fall back to the system font unnoticed.
    private(set) static var isDisplayFontRegistered = false

    /// Loads and registers the bundled Caprasimo font file via CoreText so
    /// `Font.custom("Caprasimo", size:)` resolves correctly. Must be called
    /// once, early at app launch (before any view using the display font is
    /// drawn).
    static func registerFonts() {
        guard let url = Bundle.module.url(forResource: "Caprasimo-Regular", withExtension: "ttf", subdirectory: "Fonts")
            ?? Bundle.module.url(forResource: "Caprasimo-Regular", withExtension: "ttf")
        else {
            registrationLog.error("Caprasimo-Regular.ttf not found in Bundle.module resources.")
            isDisplayFontRegistered = false
            return
        }

        var errorRef: Unmanaged<CFError>?
        let success = CTFontManagerRegisterFontsForURL(url as CFURL, .process, &errorRef)
        if success {
            isDisplayFontRegistered = true
            registrationLog.info("Registered Caprasimo font from \(url.path, privacy: .public).")
        } else {
            isDisplayFontRegistered = false
            let message = errorRef?.takeUnretainedValue().localizedDescription ?? "unknown error"
            registrationLog.error("Failed to register Caprasimo font at \(url.path, privacy: .public): \(message, privacy: .public)")
        }
    }
}

extension Font {
    /// The Caprasimo display face, used ONLY for the handful of
    /// headline/brand moments called out in the design spec. Everything
    /// else in the UI stays on the system font.
    static func caprasimo(_ size: CGFloat) -> Font {
        .custom(DS.displayFontName, size: size)
    }
}

extension SwiftUI.Color {
    init(hex: UInt32, opacity: Double = 1) {
        let r = Double((hex >> 16) & 0xFF) / 255
        let g = Double((hex >> 8) & 0xFF) / 255
        let b = Double(hex & 0xFF) / 255
        self.init(.sRGB, red: r, green: g, blue: b, opacity: opacity)
    }
}

// MARK: - Shared button / tag styles

/// Pill-shaped primary action button (terracotta background, white text).
struct PrimaryPillButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .background(
                Capsule().fill(DS.Color.accent.opacity(configuration.isPressed ? 0.85 : 1))
            )
    }
}

/// Pill-shaped secondary action button (transparent background, bordered).
struct SecondaryPillButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(DS.Color.accent700)
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .background(
                Capsule()
                    .fill(configuration.isPressed ? DS.Color.neutral200 : SwiftUI.Color.clear)
            )
            .overlay(
                Capsule().strokeBorder(DS.Color.neutral300, lineWidth: 1)
            )
    }
}

/// Small (32pt) circular icon button (neutral background, neutral icon).
struct CircularIconButtonStyle: ButtonStyle {
    var diameter: CGFloat = 32
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(DS.Color.neutral700)
            .frame(width: diameter, height: diameter)
            .background(
                Circle().fill(configuration.isPressed ? DS.Color.neutral300 : DS.Color.neutral100)
            )
    }
}

/// A small rounded status pill/tag, e.g. "Converted", "Converting", "Retry".
struct PillTag: View {
    let text: String
    var background: SwiftUI.Color
    var foreground: SwiftUI.Color
    var bold: Bool = false

    var body: some View {
        Text(text)
            .font(.system(size: 12, weight: bold ? .bold : .medium))
            .foregroundStyle(foreground)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(Capsule().fill(background))
    }
}
