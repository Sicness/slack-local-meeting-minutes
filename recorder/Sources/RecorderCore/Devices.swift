import CoreAudio
import Foundation

/// Audio device snapshot: all that logs, mic selection and the headphones/room mode need.
public struct DeviceInfo: Sendable, Equatable, Codable {
    public let id: AudioObjectID
    public let uid: String
    public let name: String
    /// Transport fourCC: bltn, blue, blea, usb , dprt, hdmi, airp, grup, virt...
    public let transport: String
    /// Stream terminal types, outputs: spkr, hdph, dprt, hdmi... inputs: micr, hmic...
    public let outputTerminals: [String]
    public let inputTerminals: [String]
    /// Built-in audio data source (ispk / hdpn / imic...), if any.
    public let outputDataSource: String?
    public let inputDataSource: String?
    public let nominalRate: Double
    public let inputChannels: Int
    public let outputChannels: Int
    public let isAlive: Bool
    /// For aggregate devices: UIDs of the active subdevices.
    public let activeSubdevices: [String]

    public var isBluetooth: Bool { transport == "blue" || transport == "blea" }

    /// Short form for logs: name [transport, outputs/inputs, source].
    public var short: String {
        var parts = [transport]
        let terms = outputChannels > 0 ? outputTerminals : inputTerminals
        if !terms.isEmpty { parts.append(terms.joined(separator: "+")) }
        if let ds = outputChannels > 0 ? outputDataSource : inputDataSource { parts.append("src=\(ds)") }
        parts.append("\(Int(nominalRate)) Hz")
        if !activeSubdevices.isEmpty { parts.append("sub=\(activeSubdevices.joined(separator: ","))") }
        if !isAlive { parts.append("DEAD") }
        return "\"\(name)\" [\(parts.joined(separator: ", "))]"
    }
}

public enum Devices {
    public static func info(id: AudioObjectID) -> DeviceInfo? {
        guard id != kAudioObjectUnknown else { return nil }
        return info(AudioHardwareDevice(id: id))
    }

    public static func info(_ device: AudioHardwareDevice) -> DeviceInfo? {
        guard let uid = try? device.uid else { return nil }
        let streams = (try? device.streams) ?? []
        var outTerms: [String] = [], inTerms: [String] = []
        for stream in streams {
            guard let dir = try? stream.direction, let term = try? stream.terminalType else { continue }
            if dir == .output { outTerms.append(fourCC(term)) } else { inTerms.append(fourCC(term)) }
        }
        let transport = fourCC((try? device.transportType) ?? 0)
        var subs: [String] = []
        if transport == "grup" {
            let agg = AudioHardwareAggregateDevice(id: device.id)
            subs = ((try? agg.activeSubdevices) ?? []).compactMap { try? $0.uid }
        }
        let inCh = ((try? device.inputStreamConfiguration) ?? []).reduce(0) { $0 + Int($1.mNumberChannels) }
        let outCh = ((try? device.outputStreamConfiguration) ?? []).reduce(0) { $0 + Int($1.mNumberChannels) }
        return DeviceInfo(
            id: device.id, uid: uid, name: (try? device.name) ?? "?",
            transport: transport,
            outputTerminals: outTerms, inputTerminals: inTerms,
            outputDataSource: device.uint32(kAudioDevicePropertyDataSource, scope: kAudioObjectPropertyScopeOutput).map(fourCC),
            inputDataSource: device.uint32(kAudioDevicePropertyDataSource, scope: kAudioObjectPropertyScopeInput).map(fourCC),
            nominalRate: (try? device.nominalSampleRate) ?? 0,
            inputChannels: inCh, outputChannels: outCh,
            isAlive: (try? device.isAlive) ?? false,
            activeSubdevices: subs)
    }

    public static var defaultInput: DeviceInfo? {
        (try? AudioHardwareSystem.shared.defaultInputDevice).flatMap { $0.flatMap(info) }
    }

    public static var defaultOutput: DeviceInfo? {
        (try? AudioHardwareSystem.shared.defaultOutputDevice).flatMap { $0.flatMap(info) }
    }
}
