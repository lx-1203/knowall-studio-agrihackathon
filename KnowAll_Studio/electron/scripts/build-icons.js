// Rasterize the project's existing original SVG brand; no third-party artwork.
const fs = require('node:fs/promises')
const path = require('node:path')
const sharp = require('sharp')
async function build() {
  const source = path.join(__dirname, '..', '..', 'frontend', 'public', 'logo.svg')
  const output = path.join(__dirname, '..', 'assets')
  await fs.mkdir(output, { recursive: true })
  await sharp(source).resize(512, 512).png().toFile(path.join(output, 'icon.png'))
  const sizes = [16, 32, 48, 64, 128, 256]
  const images = await Promise.all(sizes.map(size => sharp(source).resize(size, size).png().toBuffer()))
  const header = Buffer.alloc(6 + 16 * images.length)
  header.writeUInt16LE(1, 2); header.writeUInt16LE(images.length, 4)
  let offset = header.length
  images.forEach((image, i) => {
    const start = 6 + 16 * i
    header[start] = header[start + 1] = sizes[i] === 256 ? 0 : sizes[i]
    header.writeUInt16LE(1, start + 4); header.writeUInt16LE(32, start + 6)
    header.writeUInt32LE(image.length, start + 8); header.writeUInt32LE(offset, start + 12)
    offset += image.length
  })
  await fs.writeFile(path.join(output, 'icon.ico'), Buffer.concat([header, ...images]))
  const chunks = []
  for (const [type, size] of [['ic07', 128], ['ic08', 256], ['ic09', 512], ['ic10', 1024]]) {
    const image = await sharp(source).resize(size, size).png().toBuffer()
    const chunk = Buffer.alloc(8); chunk.write(type); chunk.writeUInt32BE(image.length + 8, 4)
    chunks.push(chunk, image)
  }
  const icns = Buffer.alloc(8); icns.write('icns'); icns.writeUInt32BE(8 + chunks.reduce((sum, chunk) => sum + chunk.length, 0), 4)
  await fs.writeFile(path.join(output, 'icon.icns'), Buffer.concat([icns, ...chunks]))
  console.log('Generated KnowAll PNG, ICO and ICNS from frontend/public/logo.svg')
}
build().catch(error => { console.error(error); process.exitCode = 1 })
