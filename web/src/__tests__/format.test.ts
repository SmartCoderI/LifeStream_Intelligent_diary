import { clock, fileSize, parseTimestamp, speakerColors, timeAgo } from "../lib/format";

test("clock", () => {
  expect(clock(75.4)).toBe("01:15");
  expect(clock(3725)).toBe("1:02:05");
  expect(clock(null)).toBe("--:--");
});

test("parseTimestamp", () => {
  expect(parseTimestamp("00:01:05")).toBe(65);
  expect(parseTimestamp("1:05")).toBe(65);
  expect(parseTimestamp("soon")).toBeNull();
  expect(parseTimestamp("")).toBeNull();
});

test("fileSize and timeAgo", () => {
  expect(fileSize(14 * 1024 ** 2)).toBe("14 MB");
  expect(fileSize(1.5 * 1024 ** 3)).toBe("1.5 GB");
  expect(timeAgo(1000, 1030)).toBe("just now");
  expect(timeAgo(0, 7200)).toBe("2 hours ago");
  expect(timeAgo(0, 86400)).toBe("1 day ago");
});

test("speakerColors are stable and distinct", () => {
  const c = speakerColors(["A", "B"]);
  expect(c.A).not.toBe(c.B);
  expect(speakerColors(["A", "B"])).toEqual(c);
});

test("run config line shows the speed settings", async () => {
  const { configLine } = await import("../lib/categories");
  expect(configLine({ device: "cuda", whisper_model: "large-v3-turbo", compute_type: "float16", asr_batch_size: 16,
                      parallel_audio: true, llm_model: "claude-opus-5-5" }))
    .toBe("CUDA · large-v3-turbo float16 · batch 16 · speakers alongside speech · claude-opus-5-5");
  expect(configLine({ device: "cpu", whisper_model: "medium.en", parallel_audio: false })).toBe("CPU · medium.en");
});
