// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MarkItDownMac",
    platforms: [
        .macOS(.v13)
    ],
    targets: [
        .executableTarget(
            name: "MarkItDownMac",
            path: "Sources/MarkItDownMac",
            resources: [
                .copy("Resources/Fonts"),
                .copy("Resources/Icon")
            ]
        )
    ]
)
