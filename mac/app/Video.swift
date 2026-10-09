// tredot-video: turns the module's frames (JPEGs, each with its timestamp) and the audio into an .mp4.
//
//   tredot-video <work folder> <clean audio.wav or -> <output.mp4>
//
// The folder has times.txt (one "000001.jpg 0" per line: frame and milliseconds) and 640x480 JPEGs.
// Instead of ffmpeg: macOS AVFoundation, H.264 encoded by the Mac's hardware, AAC for the audio, no
// licenses to comply with. The Sense camera sensor is mounted sideways: every frame is rotated a
// quarter turn clockwise (480x640). Used by mac/video.py.

import AVFoundation
import CoreGraphics
import Foundation
import ImageIO

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 4 else { fail("usage: tredot-video <folder> <audio.wav | -> <output.mp4>") }
let work = URL(fileURLWithPath: args[1])
let audioPath = args[2]
let output = URL(fileURLWithPath: args[3])

// ---------- the frames, with their timestamps ----------

let listing = (try? String(contentsOf: work.appendingPathComponent("times.txt"), encoding: .utf8)) ?? ""
let frames: [(URL, Int64)] = listing.split(separator: "\n").compactMap { line in
    let parts = line.split(separator: " ")
    guard parts.count == 2, let ms = Int64(parts[1]) else { return nil }
    return (work.appendingPathComponent(String(parts[0])), ms)
}
guard frames.count >= 2 else { fail("fewer than two frames") }

func image(_ url: URL) -> CGImage? {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(source, 0, nil)
}
guard let first = image(frames[0].0) else { fail("first frame unreadable") }
let width = first.height, height = first.width  // after the quarter turn

// ---------- the writer: H.264 video, AAC audio ----------

try? FileManager.default.removeItem(at: output)
guard let writer = try? AVAssetWriter(outputURL: output, fileType: .mp4) else { fail("cannot create \(output.path)") }
// no shouldOptimizeForNetworkUse: it only helps streaming, and leaves .sb-* copies next to the file

let video = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: width,
    AVVideoHeightKey: height,
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 1_500_000],
])
video.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: video, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
    kCVPixelBufferWidthKey as String: width,
    kCVPixelBufferHeightKey as String: height,
])
writer.add(video)

var reader: AVAssetReader?
var readerOutput: AVAssetReaderTrackOutput?
var sound: AVAssetWriterInput?
if audioPath != "-" {
    let asset = AVURLAsset(url: URL(fileURLWithPath: audioPath))
    if let track = asset.tracks(withMediaType: .audio).first, let r = try? AVAssetReader(asset: asset) {
        let out = AVAssetReaderTrackOutput(track: track, outputSettings: [AVFormatIDKey: kAudioFormatLinearPCM])
        r.add(out)
        let input = AVAssetWriterInput(mediaType: .audio, outputSettings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVNumberOfChannelsKey: 1,
            AVSampleRateKey: 16000,
            AVEncoderBitRateKey: 48000,
        ])
        input.expectsMediaDataInRealTime = false
        writer.add(input)
        reader = r
        readerOutput = out
        sound = input
    }
}

guard writer.startWriting() else { fail("writing did not start: \(writer.error?.localizedDescription ?? "?")") }
writer.startSession(atSourceTime: .zero)
reader?.startReading()

// Video and audio are written together, each when the writer is ready for it (otherwise it stalls)
let group = DispatchGroup()

func rotated(_ img: CGImage, into buffer: CVPixelBuffer) {
    CVPixelBufferLockBaseAddress(buffer, [])
    defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
    guard let ctx = CGContext(data: CVPixelBufferGetBaseAddress(buffer), width: width, height: height,
                              bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
                              space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue
                                  | CGBitmapInfo.byteOrder32Little.rawValue) else { return }
    // a quarter turn clockwise: the top-left corner goes to the top right
    ctx.translateBy(x: 0, y: CGFloat(height))
    ctx.rotate(by: -.pi / 2)
    ctx.draw(img, in: CGRect(x: 0, y: 0, width: img.width, height: img.height))
}

var next = 0
group.enter()
video.requestMediaDataWhenReady(on: DispatchQueue(label: "video")) {
    while video.isReadyForMoreMediaData {
        if next == frames.count {
            video.markAsFinished()
            group.leave()
            return
        }
        let (url, ms) = frames[next]
        next += 1
        guard let img = image(url), let pool = adaptor.pixelBufferPool else { continue }
        var buffer: CVPixelBuffer?
        CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer)
        guard let pixels = buffer else { continue }
        rotated(img, into: pixels)
        adaptor.append(pixels, withPresentationTime: CMTime(value: ms, timescale: 1000))
    }
}

if let sound = sound, let out = readerOutput {
    group.enter()
    sound.requestMediaDataWhenReady(on: DispatchQueue(label: "audio")) {
        while sound.isReadyForMoreMediaData {
            guard let sample = out.copyNextSampleBuffer() else {
                sound.markAsFinished()
                group.leave()
                return
            }
            sound.append(sample)
        }
    }
}

group.wait()
// the last frame lasts as long as the average of the others
let gap = (frames.last!.1 - frames.first!.1) / Int64(frames.count - 1)
writer.endSession(atSourceTime: CMTime(value: frames.last!.1 + gap, timescale: 1000))
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
guard writer.status == .completed else { fail("video failed: \(writer.error?.localizedDescription ?? "?")") }
