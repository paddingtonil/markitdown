import SwiftUI
import AppKit

/// The app's Settings scene content (Cmd+, / app menu "Settings…").
/// Two white rounded card sections on the warm background: default save
/// location, and the "clear queue on quit" toggle.
struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Settings")
                .font(.caprasimo(15))
                .foregroundStyle(DS.Color.text)

            saveLocationCard
            clearOnQuitCard
            statusRow

            Spacer(minLength: 0)
        }
        .padding(24)
        .frame(width: 460, height: 380, alignment: .topLeading)
        .background(DS.Color.bg)
    }

    private var saveLocationCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Default save location")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(DS.Color.text)

            HStack(spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "folder.fill")
                        .foregroundStyle(DS.Color.accent)
                    Text(settings.defaultSaveLocationPath)
                        .font(.system(size: 12.5))
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

            Text("This pre-fills the destination in the \u{201c}Save all\u{201d} sheet.")
                .font(.system(size: 11.5))
                .foregroundStyle(DS.Color.neutral700)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 24, style: .continuous).fill(Color.white))
    }

    private var clearOnQuitCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Clear the queue on quit")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(DS.Color.text)
                    Text("Off keeps converted files between launches.")
                        .font(.system(size: 11.5))
                        .foregroundStyle(DS.Color.neutral700)
                }
                Spacer()
                Toggle("", isOn: $settings.clearQueueOnQuit)
                    .labelsHidden()
                    .toggleStyle(PillToggleStyle())
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 24, style: .continuous).fill(Color.white))
    }

    private var statusRow: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(DS.Color.accent2)
                .frame(width: 8, height: 8)
            Text(AppSettings.bundledExtrasDescription)
                .font(.system(size: 12))
                .foregroundStyle(DS.Color.neutral700)
        }
        .padding(.top, 4)
    }

    private func chooseDestination() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        panel.directoryURL = settings.defaultSaveLocationURL
        if panel.runModal() == .OK, let url = panel.url {
            settings.defaultSaveLocationPath = url.path
        }
    }
}

/// A pill-shaped switch (accent when on), matching the mockup's toggle
/// styling rather than the system checkbox-style Toggle.
struct PillToggleStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Capsule()
            .fill(configuration.isOn ? DS.Color.accent : DS.Color.neutral300)
            .frame(width: 40, height: 22)
            .overlay(
                Circle()
                    .fill(Color.white)
                    .frame(width: 18, height: 18)
                    .offset(x: configuration.isOn ? 9 : -9)
                    .shadow(color: Color.black.opacity(0.15), radius: 1, x: 0, y: 1)
            )
            .animation(.easeInOut(duration: 0.15), value: configuration.isOn)
            .onTapGesture {
                configuration.isOn.toggle()
            }
    }
}
