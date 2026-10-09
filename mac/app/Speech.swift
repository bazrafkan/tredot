// tredot-speech: the fallback transcription, with macOS speech recognition, until the Whisper
// model is downloaded.
//
//   tredot-speech <audio.wav> <language|auto>   prints the text; exits 2 if permission is missing,
//                                               3 if the language cannot be recognized on this Mac
//
// "auto" uses the Mac's current locale. On-device only (requiresOnDeviceRecognition): audio never
// leaves the Mac, as with Whisper. Lives in Tredot.app/Contents/MacOS so the permission is asked
// on behalf of the app (Info.plist, NSSpeechRecognitionUsageDescription), like tredot-video.
// Used by mac/notes.py.

import Foundation
import Speech

func fail(_ message: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard args.count == 3 else { fail("usage: tredot-speech <audio.wav> <language|auto>") }
let audio = URL(fileURLWithPath: args[1])
let language = args[2]

// ---------- permission (macOS asks the first time) ----------

let asked = DispatchSemaphore(value: 0)
var status = SFSpeechRecognizer.authorizationStatus()
if status == .notDetermined {
    SFSpeechRecognizer.requestAuthorization { status = $0; asked.signal() }
    asked.wait()
}
guard status == .authorized else { fail("speech recognition not allowed (System Settings > Privacy)", 2) }

// ---------- transcription ----------

/// "it" alone is not a recognizer locale: it becomes "it-IT"
func recognitionLocale(_ language: String) -> Locale {
    if language == "auto" { return Locale.current }
    return Locale(identifier: language.contains("-") ? language : language + "-" + language.uppercased())
}

let locale = recognitionLocale(language)
guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.supportsOnDeviceRecognition else {
    fail("on-device recognition not available for \(locale.identifier)", 3)
}
recognizer.queue = OperationQueue()  // results arrive here, not on the main thread (which is waiting)

let request = SFSpeechURLRecognitionRequest(url: audio)
request.requiresOnDeviceRecognition = true
request.addsPunctuation = true
request.shouldReportPartialResults = false

let done = DispatchSemaphore(value: 0)
var text = ""
var problem: Error?
recognizer.recognitionTask(with: request) { result, error in
    if let result = result {
        text = result.bestTranscription.formattedString
        if result.isFinal { done.signal() }
    } else if let error = error {
        problem = error
        done.signal()
    }
}
done.wait()
// "No speech detected" (1110) is an empty transcript, not an error
if let problem = problem, text.isEmpty, (problem as NSError).code != 1110 {
    fail("transcription failed: \(problem.localizedDescription)")
}
print(text)
