/** Stage 3, deployed dashboard: API key form and uploads that go straight to S3. */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { processingVideo } from "./fixtures";
import { renderAt } from "./render";
import { server } from "./server";

const S3_URL = "https://lifestream-data.s3.us-west-1.amazonaws.com/uploads/trip/trip.mp4?X-Amz-Signature=abc";

function cloudMode() {
  server.use(http.get("*/api/config", () => HttpResponse.json({ mode: "cloud", auth: true })));
}

/** jsdom XHR can't send a File through MSW, so the S3 PUT goes to a fake XHR. */
function fakeS3(status = 200) {
  const sent: { method: string; url: string; headers: Record<string, string>; body: unknown } =
    { method: "", url: "", headers: {}, body: null };
  class FakeXHR {
    status = 0;
    responseText = "";
    upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    open(method: string, url: string) { sent.method = method; sent.url = url; }
    setRequestHeader(name: string, value: string) { sent.headers[name] = value; }
    send(body: unknown) {
      sent.body = body;
      setTimeout(() => {
        this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
        this.status = status;
        this.onload?.();
      }, 10);
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  return sent;
}

afterEach(() => vi.unstubAllGlobals());

test("asks for the API key when the server says it's missing, then saves it", async () => {
  cloudMode();
  server.use(http.get("*/api/days", ({ cookies }) =>
    cookies.lifestream_key === "right-key" ? HttpResponse.json([]) : HttpResponse.json({ detail: "Missing or wrong API key" }, { status: 401 })));
  const reload = vi.fn();
  vi.stubGlobal("location", { ...window.location, reload, protocol: "http:" });

  renderAt("/videos");
  const input = await screen.findByLabelText("Key");
  await userEvent.type(input, "wrong-key");
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("That key didn't work.");

  await userEvent.clear(input);
  await userEvent.type(input, "right-key");
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await vi.waitFor(() => expect(reload).toHaveBeenCalled());
  expect(document.cookie).toContain("lifestream_key=right-key");
});

test("no key form when the key works", async () => {
  cloudMode();
  renderAt("/videos");
  expect(await screen.findByRole("heading", { name: "Videos" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Key")).not.toBeInTheDocument();
});

test("uploads straight to S3 with the date as metadata, then reports the upload finished", async () => {
  cloudMode();
  const requests: { url: string; body: unknown }[] = [];
  server.use(
    http.post("*/api/videos", async ({ request }) => {
      requests.push({ url: request.url, body: await request.json() });
      return HttpResponse.json({ ...processingVideo, video_id: "trip", status: "queued", step: "uploading",
                                 presigned_put_url: S3_URL, upload_headers: { "x-amz-meta-date": "2026-11-01" } },
                               { status: 201 });
    }),
    http.post("*/api/videos/trip/uploaded", ({ request }) => {
      requests.push({ url: request.url, body: null });
      return HttpResponse.json({ ...processingVideo, video_id: "trip", status: "queued", step: "queued" }, { status: 202 });
    }),
  );
  const s3 = fakeS3();

  renderAt("/videos");
  expect(await screen.findByText(/up to 5 GB/)).toBeInTheDocument();
  await userEvent.upload(screen.getByLabelText("Video file"), new File(["data"], "trip.mp4", { type: "video/mp4" }));
  const date = screen.getByLabelText("Recording date");
  await userEvent.clear(date);
  await userEvent.type(date, "2026-11-01");
  await userEvent.click(screen.getByRole("button", { name: "Upload and process" }));
  await screen.findByRole("button", { name: "Upload and process" });

  expect(requests[0].body).toEqual({ date: "2026-11-01", filename: "trip.mp4", size_bytes: 4, multipart: true });
  expect(s3).toMatchObject({ method: "PUT", url: S3_URL, headers: { "x-amz-meta-date": "2026-11-01" } });
  expect((s3.body as File).name).toBe("trip.mp4");
  expect(requests[1].url).toMatch(/\/api\/videos\/trip\/uploaded$/);
  expect(screen.queryByText("trip.mp4")).not.toBeInTheDocument();
});

test("a failed S3 upload is shown", async () => {
  cloudMode();
  server.use(http.post("*/api/videos", () => HttpResponse.json(
    { ...processingVideo, video_id: "trip", presigned_put_url: S3_URL, upload_headers: {} }, { status: 201 })));
  fakeS3(403);
  renderAt("/videos");
  await userEvent.upload(screen.getByLabelText("Video file"), new File(["data"], "trip.mp4", { type: "video/mp4" }));
  await userEvent.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Upload to storage failed (403)");
});
