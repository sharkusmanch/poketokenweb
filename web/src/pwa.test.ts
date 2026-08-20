import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * The installable shell is the one part of this app that no component test can
 * reach: the manifest and the icons are static files that Vite copies verbatim
 * out of `public/`, and a broken reference fails *silently* — the browser just
 * shows "no icon" and refuses the install prompt.
 *
 * This suite is therefore a filesystem guard. It resolves every URL the
 * manifest and index.html name back to a real file on disk, so a renamed or
 * deleted icon fails here instead of on someone's home screen.
 *
 * It also pins the URL SHAPE, not just existence. The deployment's ingress
 * exposes exactly `/manifest.webmanifest`, `/favicon.svg` and the `/icons`
 * prefix unauthenticated; everything else is behind an auth proxy that answers
 * an icon request with an HTML login page. Moving an icon outside `/icons/`
 * would still pass an "exists on disk" check while being unfetchable in
 * production, so the path prefixes are asserted explicitly.
 */

const root = process.cwd()
const publicDir = join(root, 'public')
const manifestPath = join(publicDir, 'manifest.webmanifest')
const indexPath = join(root, 'index.html')

/** Map a root-absolute URL as served (`/icons/x.png`) to its file in public/. */
function publicFileFor(url: string): string {
  return join(publicDir, url.replace(/^\//, ''))
}

/** Width/height straight out of the PNG IHDR chunk — no image library needed. */
function pngSize(path: string): { width: number; height: number } {
  const bytes = new Uint8Array(readFileSync(path))
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
  expect([...bytes.subarray(0, 8)]).toEqual(signature)
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  // 8-byte signature, 4-byte length, 4-byte "IHDR", then width and height.
  return { width: view.getUint32(16), height: view.getUint32(20) }
}

type ManifestIcon = {
  src: string
  sizes: string
  type?: string
  purpose?: string
}

type Manifest = {
  name: string
  short_name: string
  start_url: string
  display: string
  background_color: string
  theme_color: string
  icons: ManifestIcon[]
}

const html = readFileSync(indexPath, 'utf8')

describe('manifest.webmanifest', () => {
  it('exists at the exact path the ingress exposes unauthenticated', () => {
    expect(existsSync(manifestPath)).toBe(true)
  })

  it('is valid JSON', () => {
    expect(() => JSON.parse(readFileSync(manifestPath, 'utf8'))).not.toThrow()
  })

  it('declares the required install keys', () => {
    const manifest: Manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    expect(manifest.name).toBe('PokeTokenWeb')
    expect(manifest.short_name).toBe('PokeToken')
    expect(manifest.start_url).toBe('/')
    expect(manifest.display).toBe('standalone')
    expect(manifest.background_color).toBe('#0d1117')
    expect(manifest.theme_color).toBe('#0d1117')
    expect(Array.isArray(manifest.icons)).toBe(true)
    expect(manifest.icons.length).toBeGreaterThan(0)
  })

  it('offers the 192 and 512 raster icons an installable PWA needs', () => {
    const manifest: Manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    const raster = manifest.icons.filter((icon) => icon.type === 'image/png')
    expect(raster.map((icon) => icon.sizes)).toEqual(
      expect.arrayContaining(['192x192', '512x512']),
    )
  })

  it('ships a maskable icon so Android does not letterbox it', () => {
    const manifest: Manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    const maskable = manifest.icons.filter((icon) => icon.purpose?.split(/\s+/).includes('maskable'))
    expect(maskable.length).toBeGreaterThan(0)
  })

  it('names every icon with a root-absolute URL under the /icons prefix', () => {
    const manifest: Manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    for (const icon of manifest.icons) {
      expect(icon.src.startsWith('/icons/'), `${icon.src} is outside the /icons ingress path`).toBe(
        true,
      )
    }
  })

  it('points every icon src at a file that actually exists on disk', () => {
    const manifest: Manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    const missing = manifest.icons
      .map((icon) => icon.src)
      .filter((src) => !existsSync(publicFileFor(src)))
    expect(missing).toEqual([])
  })

  it('declares sizes that match the real pixel dimensions of each PNG', () => {
    const manifest: Manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    for (const icon of manifest.icons.filter((entry) => entry.src.endsWith('.png'))) {
      const [width, height] = icon.sizes.split('x').map(Number)
      expect(pngSize(publicFileFor(icon.src)), `${icon.src}`).toEqual({ width, height })
    }
  })
})

describe('index.html PWA head', () => {
  it('links the manifest at the unauthenticated path', () => {
    expect(html).toMatch(/<link[^>]+rel="manifest"[^>]+href="\/manifest\.webmanifest"/)
  })

  it('links an apple-touch-icon', () => {
    expect(html).toMatch(/<link[^>]+rel="apple-touch-icon"[^>]+href="\/icons\/[^"]+"/)
  })

  it('links the favicon at the exact path the ingress exposes', () => {
    expect(html).toMatch(/<link[^>]+rel="icon"[^>]+href="\/favicon\.svg"/)
  })

  it('declares the iOS standalone meta tags', () => {
    expect(html).toMatch(/<meta name="apple-mobile-web-app-capable" content="yes"/)
    expect(html).toMatch(/<meta name="apple-mobile-web-app-status-bar-style" content="[^"]+"/)
  })

  it('sets the theme colour to the app background', () => {
    expect(html).toMatch(/<meta name="theme-color" content="#0d1117"/)
  })

  it('uses viewport-fit=cover so the fixed tab bar can clear the home indicator', () => {
    const viewport = /<meta name="viewport" content="([^"]+)"/.exec(html)
    expect(viewport?.[1]).toContain('viewport-fit=cover')
  })

  it('resolves every local asset it references to a real file in public/', () => {
    const hrefs = [...html.matchAll(/<link[^>]+href="(\/[^"]+)"/g)].map((match) => match[1])
    expect(hrefs.length).toBeGreaterThan(2)
    const missing = hrefs.filter((href) => !existsSync(publicFileFor(href)))
    expect(missing).toEqual([])
  })
})
