// The Tredot icon (app and .dmg): three dots on an arc on a dark background, the first one orange.
// The same shapes as the menu-bar icon and the module's screen.
//
//   icon <folder.iconset>     writes the PNGs that iconutil turns into an .icns (build_app.py)
//
// Drawn as vectors at every size (16 to 1024 px) rather than scaled down from one, so the dots stay
// round even at 16 px. The rounded square follows the macOS icon grid: 824 px out of 1024, ~185 px
// corners.

import AppKit

let args = CommandLine.arguments
guard args.count == 2 else { print("usage: icon <folder.iconset>"); exit(1) }
let folder = URL(fileURLWithPath: args[1])
try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

let background = NSColor(srgbRed: 0x1E / 255.0, green: 0x1D / 255.0, blue: 0x1B / 255.0, alpha: 1)
let orange = NSColor(srgbRed: 0xE8 / 255.0, green: 0x76 / 255.0, blue: 0x3D / 255.0, alpha: 1)
let light = NSColor(srgbRed: 0xF1 / 255.0, green: 0xEF / 255.0, blue: 0xE8 / 255.0, alpha: 1)
let arc: [(CGFloat, CGFloat)] = [(-0.68, -0.2), (0, 0.32), (0.68, -0.2)]

func png(_ pixels: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let k = CGFloat(pixels) / 1024  // drawn on a 1024 grid, then scaled
    let tile = NSRect(x: 100 * k, y: 100 * k, width: 824 * k, height: 824 * k)
    background.setFill()
    NSBezierPath(roundedRect: tile, xRadius: 185 * k, yRadius: 185 * k).fill()
    let unit = 237 * k, r = 43 * k  // the arc spans about half the square
    for (i, p) in arc.enumerated() {
        (i == 0 ? orange : light).setFill()
        let x = 512 * k + p.0 * unit, y = 512 * k - 16 * k - p.1 * unit  // y grows upward in AppKit
        NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r)).fill()
    }
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for size in [16, 32, 128, 256, 512] {
    try! png(size).write(to: folder.appendingPathComponent("icon_\(size)x\(size).png"))
    try! png(size * 2).write(to: folder.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
