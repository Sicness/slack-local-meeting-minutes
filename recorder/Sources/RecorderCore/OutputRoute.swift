import CoreAudio
import Foundation

/// Recording mode: on headphones the mic track is "Me", on speakers it is "Room".
public enum RecordingMode: String, Codable, Sendable {
    case headphones, room
}

public struct RouteDecision: Sendable, Equatable {
    public var mode: RecordingMode
    public var reason: String
    public var deviceUID: String
    public var deviceName: String
}

/// Headphones or speakers, decided by the output device:
/// any speakers (built-in, DisplayPort/HDMI monitor, AirPlay) are "Room";
/// Bluetooth, USB and the headphone jack are "Me". Device names aren't compared: they are localized.
public enum OutputRoute {
    /// Headphone terminal types: Core Audio fourCCs and USB Audio codes (as built-in audio reports them).
    static let headphoneTerminals: Set<String> = [
        "hdph",     // kAudioStreamTerminalTypeHeadphones
        "0x0302",   // USB: Headphones
        "0x0303",   // USB: Head Mounted Display Audio
        "0x0401",   // USB: Handset
        "0x0402",   // USB: Headset
    ]

    public static func classify(_ device: DeviceInfo,
                                resolve: (String) -> DeviceInfo? = { Devices.info(uid: $0) }) -> RouteDecision {
        func decision(_ mode: RecordingMode, _ reason: String) -> RouteDecision {
            RouteDecision(mode: mode, reason: "\(reason): \(device.name)", deviceUID: device.uid, deviceName: device.name)
        }
        switch device.transport {
        case "blue", "blea":
            return decision(.headphones, "Bluetooth")
        case "usb ":
            return decision(.headphones, "USB")
        case "dprt":
            return decision(.room, "DisplayPort monitor")
        case "hdmi":
            return decision(.room, "HDMI monitor")
        case "airp":
            return decision(.room, "AirPlay")
        case "bltn":
            if !headphoneTerminals.isDisjoint(with: device.outputTerminals) || device.outputDataSource == "hdpn" {
                return decision(.headphones, "headphone jack")
            }
            return decision(.room, "built-in speakers")
        case "grup":
            // Aggregate: decide by its first active output subdevice.
            for uid in device.activeSubdevices {
                guard let sub = resolve(uid), sub.outputChannels > 0, sub.transport != "grup" else { continue }
                var d = classify(sub, resolve: resolve)
                d.reason = "aggregate \"\(device.name)\" -> \(d.reason)"
                d.deviceUID = device.uid
                d.deviceName = device.name
                return d
            }
            return decision(.headphones, "aggregate without a clear output, assuming headphones")
        default:
            return decision(.headphones, "unknown transport \"\(device.transport)\", assuming headphones")
        }
    }
}

extension Devices {
    public static func info(uid: String) -> DeviceInfo? {
        guard let device = try? AudioHardwareSystem.shared.device(forUID: uid) else { return nil }
        return info(device)
    }
}
