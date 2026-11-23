import Foundation
import RecorderCore

let arguments = Array(CommandLine.arguments.dropFirst())

switch arguments.first {
case "probe":
    Paths.securePermissions()
    Probe.run(Array(arguments.dropFirst()))
case "record":
    Paths.securePermissions()
    MainActor.assumeIsolated { HeadlessRecord(args: Array(arguments.dropFirst())).run() }
case "help", "--help", "-h":
    FileHandle.standardError.write(Data("""
    Usage:
      MeetingRecorder                                     menu bar app
      MeetingRecorder record [--seconds N] [--root DIR] [--bundle-prefix ID]... [--mute] [--pid-file F]
                                                          record from the terminal (run via scripts/record.sh)
      MeetingRecorder probe watch                         Slack processes and devices (no permissions needed)
      MeetingRecorder probe tap --seconds N --out DIR     prototype: two taps and the microphone
      MeetingRecorder probe writer-test --seconds N --out DIR [--defer-sizes]   synthetic CAF recording

    """.utf8))
    exit(0)
default:
    // No arguments (or only those macOS adds at launch): menu bar app.
    MainActor.assumeIsolated { MenuApp.run() }
}
