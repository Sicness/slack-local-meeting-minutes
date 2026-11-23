// swift-tools-version: 6.0
import PackageDescription

// ma-diarize: splits a track into voices and computes span embeddings for `ma` (FluidAudio, CoreML).
// Separate from recorder/, which doesn't need FluidAudio.
let package = Package(
    name: "MeetingDiarizer",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/FluidInference/FluidAudio.git", exact: "0.18.3"),
    ],
    targets: [
        .executableTarget(
            name: "ma-diarize",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")]
        ),
    ]
)
