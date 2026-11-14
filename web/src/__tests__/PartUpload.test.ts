/** Stage 3: large files go to S3 in parts, a few at a time. */
import { http, HttpResponse } from "msw";
import { api, resetConfig, UploadCancelled, uploadParts } from "../lib/api";
import { processingVideo } from "./fixtures";
import { server } from "./server";

interface Sent { url: string; size: number; headers: Record<string, string> }

/** Fake S3 for part PUTs: answers each part after `delay` ms with an ETag, or fails `failures[url]` times. */
function fakeS3({ delay = 5, failures = {} as Record<string, number>, status = 200 } = {}) {
  const sent: Sent[] = [];
  let active = 0;
  const stats = { maxActive: 0 };
  class FakeXHR {
    status = 0;
    url = "";
    headers: Record<string, string> = {};
    upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    timer: ReturnType<typeof setTimeout> | null = null;
    open(_method: string, url: string) { this.url = url; }
    setRequestHeader(name: string, value: string) { this.headers[name] = value; }
    getResponseHeader(name: string) { return name === "ETag" ? `"etag-${this.url.slice(-1)}"` : null; }
    abort() { if (this.timer) clearTimeout(this.timer); active--; this.onabort?.(); }
    send(body: Blob) {
      sent.push({ url: this.url, size: body.size, headers: this.headers });
      active++;
      stats.maxActive = Math.max(stats.maxActive, active);
      this.timer = setTimeout(() => {
        active--;
        this.timer = null;
        this.upload.onprogress?.({ lengthComputable: true, loaded: body.size, total: body.size });
        if (failures[this.url]) {
          failures[this.url]--;
          this.onerror?.();
          return;
        }
        this.status = status;
        this.onload?.();
      }, delay);
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  return { sent, stats };
}

const upload = (n: number, partSize = 10) => ({
  upload_id: "u1", part_size: partSize, part_urls: Array.from({ length: n }, (_, i) => `https://s3/part?n=${i + 1}`),
});

afterEach(() => vi.unstubAllGlobals());

test("sends every part, four at a time, and returns their ETags in order", async () => {
  const { sent, stats } = fakeS3();
  const progress: number[] = [];
  const parts = await uploadParts(new Blob(["x".repeat(95)]), upload(10), (f) => progress.push(f));
  expect(parts).toEqual(Array.from({ length: 10 }, (_, i) => ({ part_number: i + 1, etag: `"etag-${(i + 1) % 10}"` })));
  expect(sent.map((s) => s.size).sort((a, b) => a - b)).toEqual([5, 10, 10, 10, 10, 10, 10, 10, 10, 10]);
  expect(sent.every((s) => Object.keys(s.headers).length === 0)).toBe(true);  // the date was set when it started
  expect(stats.maxActive).toBe(4);
  expect(progress.at(-1)).toBe(1);
  expect(progress).toEqual([...progress].sort((a, b) => a - b));
});

test("a part that fails is tried again", async () => {
  const { sent } = fakeS3({ failures: { "https://s3/part?n=2": 2 } });
  const parts = await uploadParts(new Blob(["x".repeat(30)]), upload(3), () => {});
  expect(parts.map((p) => p.part_number)).toEqual([1, 2, 3]);
  expect(sent.filter((s) => s.url.endsWith("n=2"))).toHaveLength(3);
});

test("a part that keeps failing stops the upload", async () => {
  fakeS3({ status: 403 });
  await expect(uploadParts(new Blob(["x".repeat(30)]), upload(3), () => {})).rejects.toThrow("Upload to storage failed (403)");
});

test("cancelling stops every transfer", async () => {
  fakeS3({ delay: 1000 });
  const controller = new AbortController();
  const done = uploadParts(new Blob(["x".repeat(100)]), upload(10), () => {}, controller.signal);
  controller.abort();
  await expect(done).rejects.toBeInstanceOf(UploadCancelled);
});

test("uploadVideo asks for a part upload and sends the ETags back when it's done", async () => {
  server.use(http.get("*/api/config", () => HttpResponse.json({ mode: "cloud", auth: false })));
  resetConfig();
  const finished: unknown[] = [];
  server.use(
    http.post("*/api/videos", () => HttpResponse.json(
      { ...processingVideo, video_id: "trip", presigned_put_url: "https://s3/whole", upload_headers: {},
        multipart: upload(2) }, { status: 201 })),
    http.post("*/api/videos/trip/uploaded", async ({ request }) => {
      finished.push(await request.json());
      return HttpResponse.json({ ...processingVideo, video_id: "trip", step: "queued" }, { status: 202 });
    }),
  );
  const { sent } = fakeS3();
  await api.uploadVideo(new File(["x".repeat(15)], "trip.mp4"), "2026-11-01", () => {});
  expect(sent.map((s) => s.url).sort()).toEqual(["https://s3/part?n=1", "https://s3/part?n=2"]);
  expect(finished).toEqual([{ parts: [{ part_number: 1, etag: '"etag-1"' }, { part_number: 2, etag: '"etag-2"' }] }]);
  resetConfig();
});
