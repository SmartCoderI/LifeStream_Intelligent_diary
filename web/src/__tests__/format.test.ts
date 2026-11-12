import { clock, fileSize, parseTimestamp, speakerColors, timeAgo } from "../format";

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
