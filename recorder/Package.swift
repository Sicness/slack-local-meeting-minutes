// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "MeetingRecorder",
    platforms: [.macOS(.v15)],
    targets: [
        // Core Audio, file writing, permissions. No AppKit and no MainActor isolation:
        // some of this code runs on the real-time audio thread.
        .target(
            name: "RecorderCore",
            linkerSettings: [
                .linkedFramework("CoreAudio"),
                .linkedFramework("AudioToolbox"),
                .linkedFramework("AVFoundation"),
            ]
        ),
        // Menu bar app; `MeetingRecorder probe ...` is a prototype and diagnostics tool.
        .executableTarget(
            name: "MeetingRecorder",
            dependencies: ["RecorderCore"],
            linkerSettings: [.linkedFramework("AppKit")]
        ),
        // Run with scripts/test.sh (without Xcode, swift test can't find Swift Testing).
        .testTarget(name: "RecorderCoreTests", dependencies: ["RecorderCore"]),
    ]
)
