import Testing
@testable import RecorderCore

func device(_ name: String, transport: String, out: [String] = [], outSource: String? = nil,
            outputChannels: Int = 2, inputChannels: Int = 1, subdevices: [String] = []) -> DeviceInfo {
    DeviceInfo(id: 1, uid: "uid-\(name)", name: name, transport: transport,
               outputTerminals: out, inputTerminals: [], outputDataSource: outSource, inputDataSource: nil,
               nominalRate: 48_000, inputChannels: inputChannels, outputChannels: outputChannels,
               isAlive: true, activeSubdevices: subdevices)
}

@Suite("headphones or room")
struct RouteTests {
    @Test("built-in speakers are room (USB code and fourCC)")
    func builtInSpeakers() {
        #expect(OutputRoute.classify(device("Динамики MacBook Pro", transport: "bltn", out: ["0x0301"], outSource: "ispk")).mode == .room)
        #expect(OutputRoute.classify(device("Speakers", transport: "bltn", out: ["spkr"])).mode == .room)
    }

    @Test("headphone jack is headphones")
    func headphoneJack() {
        #expect(OutputRoute.classify(device("Внешние наушники", transport: "bltn", out: ["0x0302"])).mode == .headphones)
        #expect(OutputRoute.classify(device("Headphones", transport: "bltn", out: ["hdph"])).mode == .headphones)
        #expect(OutputRoute.classify(device("Built-in", transport: "bltn", out: ["0x0301"], outSource: "hdpn")).mode == .headphones)
    }

    @Test("Bluetooth and USB are headphones, even for a speaker")
    func bluetoothUSB() {
        #expect(OutputRoute.classify(device("BT Headset", transport: "blue", out: ["hdph"])).mode == .headphones)
        #expect(OutputRoute.classify(device("BT LE", transport: "blea")).mode == .headphones)
        #expect(OutputRoute.classify(device("USB speaker", transport: "usb ", out: ["0x0301"])).mode == .headphones)
    }

    @Test("monitor and AirPlay are room")
    func monitors() {
        #expect(OutputRoute.classify(device("LG", transport: "dprt", out: ["dprt"])).mode == .room)
        #expect(OutputRoute.classify(device("TV", transport: "hdmi")).mode == .room)
        #expect(OutputRoute.classify(device("HomePod", transport: "airp")).mode == .room)
    }

    @Test("aggregate follows its first output subdevice")
    func aggregate() {
        let speakers = device("Динамики", transport: "bltn", out: ["0x0301"])
        let mic = device("Mic", transport: "usb ", outputChannels: 0)
        let agg = device("Multi", transport: "grup", subdevices: [mic.uid, speakers.uid])
        let resolved = OutputRoute.classify(agg) { uid in [mic, speakers].first { $0.uid == uid } }
        #expect(resolved.mode == .room)
        #expect(resolved.deviceUID == agg.uid)
        #expect(resolved.reason.contains("aggregate"))
    }

    @Test("unknown transport is headphones (the common case)")
    func unknown() {
        #expect(OutputRoute.classify(device("Virtual", transport: "virt")).mode == .headphones)
    }
}

@Suite("microphone selection")
struct MicPolicyTests {
    let bt = device("BT Headset", transport: "blue")
    let builtIn = device("Микрофон MacBook Pro", transport: "bltn")
    let usb = device("USB mic", transport: "usb ")

    @Test("Slack took Bluetooth: record from it")
    func followSlack() {
        let d = MicPolicy.decide(slackHoldsMic: true, slackInput: bt, current: nil, defaultInput: bt, slackReleasedFor: 0)
        #expect(d == .use(bt, reason: "Slack device"))
        #expect(MicPolicy.decide(slackHoldsMic: true, slackInput: bt, current: bt, defaultInput: bt, slackReleasedFor: 0) == .keep)
    }

    @Test("Slack changed the mic: switch")
    func slackSwitched() {
        let d = MicPolicy.decide(slackHoldsMic: true, slackInput: usb, current: builtIn, defaultInput: builtIn, slackReleasedFor: 0)
        #expect(d == .use(usb, reason: "Slack device"))
    }

    @Test("bounce on joining: Slack released for a second, keep it")
    func debounce() {
        let d = MicPolicy.decide(slackHoldsMic: false, slackInput: nil, current: bt, defaultInput: bt, slackReleasedFor: 1)
        #expect(d == .keep)
    }

    @Test("Slack released Bluetooth 3+ s ago: release so the headset leaves call mode")
    func releaseBluetooth() {
        let d = MicPolicy.decide(slackHoldsMic: false, slackInput: nil, current: bt, defaultInput: bt, slackReleasedFor: 3.5)
        #expect(d == .stop(reason: "Slack released the Bluetooth mic"))
    }

    @Test("Slack released Bluetooth with built-in as default: switch to built-in")
    func releaseToDefault() {
        let d = MicPolicy.decide(slackHoldsMic: false, slackInput: nil, current: bt, defaultInput: builtIn, slackReleasedFor: 10)
        #expect(d == .use(builtIn, reason: "default input"))
    }

    @Test("before a call, never open Bluetooth ourselves")
    func noBluetoothBeforeSlack() {
        #expect(MicPolicy.decide(slackHoldsMic: false, slackInput: nil, current: nil, defaultInput: bt, slackReleasedFor: .infinity) == .keep)
    }

    @Test("before a call, use the default input and follow its changes")
    func defaultInput() {
        #expect(MicPolicy.decide(slackHoldsMic: false, slackInput: nil, current: nil, defaultInput: builtIn, slackReleasedFor: .infinity)
                == .use(builtIn, reason: "default input"))
        #expect(MicPolicy.decide(slackHoldsMic: false, slackInput: nil, current: builtIn, defaultInput: usb, slackReleasedFor: .infinity)
                == .use(usb, reason: "default input"))
    }

    @Test("Slack device not visible: use the default input, leave Bluetooth alone")
    func invisibleSlackDevice() {
        #expect(MicPolicy.decide(slackHoldsMic: true, slackInput: nil, current: nil, defaultInput: builtIn, slackReleasedFor: 0)
                == .use(builtIn, reason: "default input"))
        #expect(MicPolicy.decide(slackHoldsMic: true, slackInput: nil, current: bt, defaultInput: bt, slackReleasedFor: 0) == .keep)
    }
}

@Suite("watchdogs")
struct WatchTests {
    @Test("10 s of digital silence during a call")
    func silence() {
        var w = SilenceWatch(limit: 10)
        for _ in 0..<9 {
            let fired = w.feed(expectSound: true, silent: true)
            #expect(!fired)
        }
        let fired = w.feed(expectSound: true, silent: true)
        #expect(fired)
        #expect(w.seconds == 0)
    }

    @Test("audio or the end of a call resets the counter")
    func silenceReset() {
        var w = SilenceWatch(limit: 3)
        _ = w.feed(expectSound: true, silent: true)
        _ = w.feed(expectSound: true, silent: true)
        let afterSound = w.feed(expectSound: true, silent: false)
        #expect(!afterSound)
        _ = w.feed(expectSound: true, silent: true)
        let afterCall = w.feed(expectSound: false, silent: true)
        #expect(!afterCall)
        #expect(w.seconds == 0)
    }

    @Test("an output change counts after settling, a short blip doesn't")
    func routeSettle() {
        var t = RouteTracker(settle: 1)
        let a = RouteDecision(mode: .headphones, reason: "a", deviceUID: "a", deviceName: "a")
        let b = RouteDecision(mode: .room, reason: "b", deviceUID: "b", deviceName: "b")
        var results: [RouteDecision?] = []
        for (d, time) in [(a, 0.0), (b, 1), (a, 1.25), (b, 2), (b, 2.5), (b, 3)] {
            results.append(t.feed(d, at: time))
        }
        // a blip to b and straight back doesn't count; b held for 1 s does
        #expect(results == [a, nil, nil, nil, nil, b])
        #expect(t.current == b)
    }
}

@Test func autoRecordNeedsChoiceAndNotice() {
    #expect(!RecordingConsent.autoRecordActive(stored: nil, noticeAccepted: true))     // off by default
    #expect(!RecordingConsent.autoRecordActive(stored: true, noticeAccepted: false))
    #expect(!RecordingConsent.autoRecordActive(stored: false, noticeAccepted: true))
    #expect(RecordingConsent.autoRecordActive(stored: true, noticeAccepted: true))
    #expect(RecordingConsent.needsNoticeAtLaunch(stored: true, noticeAccepted: false))  // upgrade from an older version
    #expect(!RecordingConsent.needsNoticeAtLaunch(stored: nil, noticeAccepted: false))
    #expect(!RecordingConsent.needsNoticeAtLaunch(stored: true, noticeAccepted: true))
}
