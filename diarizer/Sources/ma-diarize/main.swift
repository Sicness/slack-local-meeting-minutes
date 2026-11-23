// ma-diarize diarize <wav> <out.json> [--threshold x]   - voice segments and the mean embedding of each
// ma-diarize embed <wav> <spans.json> <out.json>        - an embedding for each span [[start, end], ...] (seconds)
// Runs on CPU and Neural Engine, not GPU: processing must not slow Slack down.
import CoreML
import FluidAudio
import Foundation

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data("ma-diarize: \(message)\n".utf8))
    exit(code)
}

let usage = "usage: ma-diarize diarize <wav> <out.json> [--threshold x] | embed <wav> <spans.json> <out.json>"
var args = Array(CommandLine.arguments.dropFirst())
var threshold: Double?
if let i = args.firstIndex(of: "--threshold") {
    guard i + 1 < args.count, let v = Double(args[i + 1]) else { fail(usage, code: 2) }
    threshold = v
    args.removeSubrange(i...(i + 1))
}
guard let command = args.first else { fail(usage, code: 2) }

/// Model snapshot: embeddings from different snapshots are never mixed in the registry.
func modelSnapshot() -> String {
    let url = OfflineDiarizerModels.defaultModelsDirectory()
        .appendingPathComponent("speaker-diarization/provenance.json")
    guard let data = try? Data(contentsOf: url),
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let snap = obj["artifact_snapshot"] as? String
    else { return "unknown" }
    return "fluidaudio-offline@\(snap)"
}

func write(_ object: [String: Any], to path: String) throws {
    let data = try JSONSerialization.data(withJSONObject: object)
    let url = URL(fileURLWithPath: path)
    let tmp = url.appendingPathExtension("tmp")
    try data.write(to: tmp)
    _ = try FileManager.default.replaceItemAt(url, withItemAt: tmp)
}

do {
    var config = OfflineDiarizerConfig.default
    if let threshold { config.clustering.threshold = threshold }
    let manager = OfflineDiarizerManager(config: config)
    let mlc = MLModelConfiguration()
    mlc.computeUnits = .cpuAndNeuralEngine
    let started = Date()
    // The first run downloads models (~23 MB) to ~/Library/Application Support/FluidAudio.
    let models = try await OfflineDiarizerModels.load(
        from: OfflineDiarizerModels.defaultModelsDirectory(), configuration: mlc)
    manager.initialize(models: models)
    let wavIndex = 1
    guard args.count > wavIndex else { fail(usage, code: 2) }
    let loaded = try AudioSourceFactory().makeDiskBackedSource(
        from: URL(fileURLWithPath: args[wavIndex]), targetSampleRate: config.segmentation.sampleRate)
    defer { loaded.source.cleanup() }
    let sampleRate = Double(config.segmentation.sampleRate)
    let audioSeconds = Double(loaded.source.sampleCount) / sampleRate
    var out: [String: Any] = ["model": modelSnapshot(), "audio_seconds": audioSeconds]

    switch command {
    case "diarize":
        guard args.count == 3 else { fail(usage, code: 2) }
        let result = try await manager.process(audioSource: loaded.source, audioLoadingSeconds: loaded.loadDuration)
        out["segments"] = result.segments.map {
            ["start": Double($0.startTimeSeconds), "end": Double($0.endTimeSeconds), "speaker": $0.speakerId,
             "quality": Double($0.qualityScore)] as [String: Any]
        }
        out["speakers"] = result.speakerDatabase ?? [:]
        out["seconds"] = Date().timeIntervalSince(started)
        try write(out, to: args[2])
    case "embed":
        guard args.count == 4 else { fail(usage, code: 2) }
        let raw = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2])))
        guard let pairs = raw as? [[Double]], pairs.allSatisfy({ $0.count == 2 }) else {
            fail("\(args[2]): expected a list of [start, end]")
        }
        // FluidAudio rejects empty spans or spans past the end, so clip them; the caller extends short ones.
        let spans: [Range<TimeInterval>] = try pairs.map { p in
            let a = max(0, p[0]), b = min(audioSeconds, p[1])
            guard a < b else { throw NSError(domain: "ma-diarize", code: 3, userInfo: [NSLocalizedDescriptionKey: "empty span \(p)"]) }
            return a..<b
        }
        out["embeddings"] = spans.isEmpty ? [] : try await manager.embedSpans(audioSource: loaded.source, spans: spans)
        out["seconds"] = Date().timeIntervalSince(started)
        try write(out, to: args[3])
    default:
        fail(usage, code: 2)
    }
} catch {
    fail(error.localizedDescription)
}
