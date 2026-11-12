import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";

test.use({
  launchOptions: {
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
    ],
  },
});

const password = "test-ui-password-12345";
async function signIn(page: Page) {
  const health = await (await page.request.get("/api/health")).json();
  const response = await page.request.post(
    health.configured ? "/api/login" : "/api/setup",
    { data: { password } },
  );
  expect(response.ok()).toBeTruthy();
}

test("first launch goes directly to setup and sign-out returns to sign-in", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Set up AttendAI" }),
  ).toBeVisible();
  await expect(page.getByText(/demo preview|sample people/i)).toHaveCount(0);
  await page.getByLabel("Create an administrator password").fill("short");
  await page
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("at least 12 characters");
  await page.getByLabel("Create an administrator password").fill(password);
  await page
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Live Monitor", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Connect your first camera" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Sign out", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Sign in to AttendAI" }),
  ).toBeVisible();
});

test("sign-in and server errors give a recovery path", async ({ page }) => {
  await page.route("**/api/health", (route) =>
    route.fulfill({ status: 503, body: "Offline" }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "AttendAI is offline" }),
  ).toBeVisible();
  await page.unroute("**/api/health");
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Sign in to AttendAI" }),
  ).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill("wrong-password-123");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Password is incorrect");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Live Monitor", exact: true }),
  ).toBeVisible();
});

test("monitor separates review and sightings failures from camera health", async ({
  page,
}) => {
  await signIn(page);
  let failReview = true;
  let failSightings = true;
  await page.route("**/api/review?summary=true", (route) =>
    failReview
      ? route.fulfill({ status: 503, body: "Unavailable" })
      : route.continue(),
  );
  await page.route("**/api/sightings?*", (route) =>
    failSightings
      ? route.fulfill({ status: 503, body: "Unavailable" })
      : route.continue(),
  );
  await page.goto("/");
  const readiness = page.getByRole("region", { name: "Attendance readiness" });
  await expect(readiness).toContainText("0 live · 0 analyzing");
  const queue = page.getByRole("complementary", { name: "Newly seen people" });
  await expect(queue.getByRole("alert")).toContainText(
    "The review queue could not refresh",
  );
  await page.getByText("Recent sightings", { exact: true }).click();
  const timeline = page.locator(".attend-sightings");
  await expect(timeline.getByRole("alert")).toContainText(
    "Recent sightings could not refresh",
  );
  await expect(readiness).toContainText("0 live · 0 analyzing");
  failReview = false;
  await queue.getByRole("button", { name: "Retry" }).click();
  await expect(queue.getByText("No faces waiting for a name.")).toBeVisible();
  await expect(timeline.getByRole("alert")).toBeVisible();
  failSightings = false;
  await timeline.getByRole("button", { name: "Retry" }).click();
  await expect(
    timeline.getByText("No sightings match this view."),
  ).toBeVisible();
});

test("all workspace pages fit narrow screens and mobile menu exposes sign-out", async ({
  page,
}) => {
  await signIn(page);
  await page.setViewportSize({ width: 360, height: 800 });
  for (const [hash, heading] of [
    ["scanner", "Live Monitor"],
    ["people", "People"],
    ["review", "Name People"],
    ["attendance", "Attendance records"],
    ["overview", "Attendance overview"],
    ["settings", "Settings"],
    ["activity", "Activity log"],
  ]) {
    await page.goto("/#" + hash);
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    const audit = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(
      audit.violations.map((item) => ({
        id: item.id,
        targets: item.nodes.map((node) => node.target),
      })),
      hash,
    ).toEqual([]);
    if (hash === "settings") {
      const advanced = page.getByText("Advanced settings", { exact: true });
      await expect(page.getByLabel("Recognition threshold")).not.toBeVisible();
      await advanced.focus();
      await page.keyboard.press("Enter");
      await expect(page.getByLabel("Recognition threshold")).toBeVisible();
      await expect(
        page.getByLabel("Collect unknown faces for review"),
      ).toBeVisible();
      await page.screenshot({
        path: "test-results/settings-advanced-phone.png",
        fullPage: true,
      });
      const threshold = page.getByLabel("Recognition threshold");
      const initial = await threshold.inputValue();
      await threshold.fill("0.55");
      await advanced.click();
      await advanced.click();
      await expect(threshold).toHaveValue("0.55");
      await page.getByRole("button", { name: "Discard changes" }).click();
      await expect(threshold).toHaveValue(initial);
    }
  }
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("button", { name: "Sign out", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("dialog")
    .getByRole("link", { name: "Live Monitor", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Share laptop camera" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/live-monitor-mobile.png",
    fullPage: true,
  });
});

test("live review shows capture progress and opens the group the camera just saved", async ({
  page,
}) => {
  await signIn(page);
  let captured = false;
  const id = "live-review-fixture";
  const photo = readFileSync("tests/fixtures/face.jpg").toString("base64");
  const now = new Date().toISOString();
  await page.route("**/api/cameras", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "camera-fixture",
            name: "Test entrance",
            location: "Pool",
            source_type: "network",
            role: "in",
            state: "running",
            analysis_state: "current",
            enabled: true,
            preview_ready: true,
            tracks: [
              captured
                ? {
                    track: "live-track",
                    label: "Unknown person",
                    state: "unknown",
                    review_status: "ready",
                    review_group_id: id,
                  }
                : {
                    track: "live-track",
                    label: "Collecting face samples",
                    state: "verifying",
                    review_status: "collecting",
                    review_samples: 2,
                    needed: 3,
                  },
            ],
          },
        ],
      },
    }),
  );
  await page.route("**/api/review?*", (route) =>
    route.fulfill({
      json: {
        items: captured
          ? [
              {
                id,
                sample: photo,
                samples: [photo, photo, photo],
                sample_count: 3,
                sightings: 1,
                first_seen: now,
                last_seen: now,
              },
            ]
          : [],
        total: captured ? 1 : 0,
        page: 1,
      },
    }),
  );
  await page.goto("/#scanner");
  const strip = page.getByRole("region", { name: "Live people and review" });
  await expect(strip).toContainText("Collecting samples · 2/3");
  await expect(strip).toContainText("0 waiting");
  captured = true;
  await expect(strip).toContainText("Face ready to name");
  await expect(strip).toContainText("1 waiting", { timeout: 2600 });
  const lookup = page.waitForRequest(
    (request) => new URL(request.url()).searchParams.get("group_id") === id,
  );
  await strip.getByRole("button", { name: "Name live face" }).click();
  await lookup;
  await expect(
    page.getByRole("heading", { name: "Name person #live-r" }),
  ).toBeVisible();
});

test("latest person stays visible above six live cameras at desktop and phone sizes", async ({
  page,
}) => {
  await signIn(page);
  let seen = false;
  await page.route("**/api/cameras", (route) =>
    route.fulfill({
      json: {
        items: Array.from({ length: 6 }, (_, index) => ({
          id: `camera-${index + 1}`,
          name: `Camera ${index + 1}`,
          location: "Pool",
          source_type: "network",
          role: "in",
          state: "running",
          analysis_state: "current",
          enabled: true,
          preview_ready: true,
          processed_fps: 2,
          analysis_size: [3840, 2160],
          tracks:
            index === 5 && seen
              ? [
                  {
                    track: "new-track",
                    label: "Newly seen person",
                    state: "unknown",
                  },
                ]
              : [],
        })),
      },
    }),
  );
  await page.route("**/api/cameras/*/snapshot?**", (route) =>
    route.fulfill({
      path: "tests/fixtures/face.jpg",
      contentType: "image/jpeg",
    }),
  );
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/#scanner");
  const strip = page.getByRole("region", { name: "Live people and review" });
  await expect(strip).toContainText("No faces in view");
  const tiles = page.getByRole("article", { name: /^Camera [1-6]$/ });
  await expect(tiles).toHaveCount(6);
  const firstRow = await tiles.evaluateAll(
    (items) =>
      items.filter(
        (item) =>
          item.getBoundingClientRect().top ===
          items[0].getBoundingClientRect().top,
      ).length,
  );
  expect(firstRow).toBe(3);
  await page.screenshot({ path: "test-results/camera-wall-desktop.png" });

  await page.setViewportSize({ width: 360, height: 640 });
  await page.evaluate(() => window.scrollTo(0, 700));
  await expect
    .poll(() =>
      strip.evaluate((element) =>
        Math.round(element.getBoundingClientRect().top),
      ),
    )
    .toBe(0);
  seen = true;
  await expect(strip.getByText("Newly seen person")).toBeInViewport();
  await expect(strip.getByText("Camera 6")).toBeInViewport();
  await strip.getByRole("button", { name: "View camera" }).click();
  await expect(page.getByRole("article", { name: "Camera 6" })).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "test-results/live-monitor-sticky-phone.png" });
});

test("desktop monitor, camera setup, and mobile monitor pass automated accessibility", async ({
  page,
}) => {
  await signIn(page);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Live Monitor", exact: true }),
    ).toBeVisible();
    const scan = async () => {
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      expect(
        results.violations.map((v) => ({
          id: v.id,
          nodes: v.nodes.map((n) => ({
            target: n.target,
            summary: n.failureSummary,
          })),
        })),
      ).toEqual([]);
    };
    await scan();
    await page.getByRole("button", { name: "Share laptop camera" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await scan();
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("button", { name: "Share laptop camera" }),
    ).toBeFocused();
  }
});

test("IP camera setup keeps credentials private and lets the administrator remove the source", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Connect a camera", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Connect & start monitoring" })
    .click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByLabel("Camera name").fill("Test Entrance");
  await page.getByLabel("Room or location").fill("Test Room");
  await expect(page.getByLabel("Start camera in")).toHaveValue("setup");
  await page
    .getByLabel("Stream address", { exact: true })
    .fill("rtsp://operator:private-password@192.0.2.1:554/live");
  await expect(
    page.getByLabel("Stream address", { exact: true }),
  ).toHaveAttribute("type", "password");
  await page
    .getByText("Attendance and camera options", { exact: true })
    .click();
  await page
    .getByLabel("When a known person appears")
    .selectOption("entry_exit");
  await expect(page.getByLabel("Attendance trigger")).toHaveValue("line");
  await page
    .getByText("I am authorized to connect this camera", { exact: false })
    .click();
  await page.route("**/api/cameras/*/start", (route) =>
    route.fulfill({ json: { ok: true } }),
  );
  await page
    .getByRole("button", { name: "Connect & start monitoring" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Test Entrance", exact: true }),
  ).toBeVisible();
  expect(await (await page.request.get("/api/cameras")).text()).not.toContain(
    "private-password",
  );
  await page.getByRole("button", { name: "Manage Test Entrance" }).click();
  await expect(page.getByLabel("Operating mode")).toHaveValue("setup");
  const distance = page.getByRole("checkbox", {
    name: /Room-distance detection/,
  });
  await expect(distance).not.toBeVisible();
  await page.getByText("Advanced camera settings", { exact: true }).click();
  await distance.click();
  await expect(distance).toBeChecked();
  await page.locator(".attend-remove-camera summary").click();
  await page
    .getByRole("button", { name: "Remove camera", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Test Entrance", exact: true }),
  ).not.toBeVisible();
});

test("browser permission denial leads to a prefilled Windows camera connection", async ({
  page,
}) => {
  await signIn(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
      value: () =>
        Promise.reject(new DOMException("Denied", "NotAllowedError")),
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Share laptop camera" }).click();
  await page
    .getByLabel("I’m authorized to use this camera", { exact: false })
    .check();
  await page.getByRole("button", { name: "Allow and share" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "permission was denied",
  );
  await page
    .getByRole("button", { name: "Connect through Windows instead" })
    .click();
  await expect(page.getByLabel("USB device index")).toHaveValue("0");
  await expect(page.getByLabel("Camera name")).toHaveValue("Laptop camera");
});

test.describe("browser camera with Chromium test video", () => {
  test("high detail requests 4K and reports the webcam’s real fallback resolution", async ({
    page,
  }) => {
    await signIn(page);
    await page.addInitScript(() => {
      const open = navigator.mediaDevices.getUserMedia.bind(
        navigator.mediaDevices,
      );
      navigator.mediaDevices.getUserMedia = (constraints) => {
        const video = constraints?.video as MediaTrackConstraints;
        document.documentElement.dataset.requestedWidth = String(
          (video.width as ConstrainULongRange).ideal,
        );
        return open({
          video: { width: { exact: 640 }, height: { exact: 480 } },
          audio: false,
        });
      };
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Share laptop camera" }).click();
    await page.getByText("Advanced camera settings", { exact: true }).click();
    await page
      .getByRole("checkbox", { name: /High detail for distant faces/ })
      .check();
    await page
      .getByLabel("I’m authorized to use this camera", { exact: false })
      .check();
    const response = page.waitForResponse(
      (result) => result.url().endsWith("/api/cameras/browser") && result.ok(),
    );
    await page.getByRole("button", { name: "Allow and share" }).click();
    expect((await (await response).json()).frame_max_edge).toBe(3840);
    await expect(page.locator("html")).toHaveAttribute(
      "data-requested-width",
      "3840",
    );
    await expect(
      page.getByText(
        "High detail requested. This camera is supplying 640 × 480.",
      ),
    ).toBeVisible();
    await page.getByRole("button", { name: "Manage Laptop camera" }).click();
    await page.getByText("Advanced camera settings", { exact: true }).click();
    await expect(page.getByText(/Analysis 640 × 480/)).toBeVisible();
    await page.keyboard.press("Escape");
    await page
      .getByRole("button", { name: "Stop sharing", exact: true })
      .click();
  });

  test("shares real browser frames with the server, survives navigation, and stops", async ({
    page,
  }) => {
    await signIn(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Share laptop camera" }).click();
    await page
      .getByLabel("I’m authorized to use this camera", { exact: false })
      .check();
    const frame = page.waitForResponse(
      (r) => r.url().endsWith("/browser-frame") && r.ok(),
    );
    await page.getByRole("button", { name: "Allow and share" }).click();
    await frame;
    const video = page.getByLabel("Live video from Laptop camera");
    await expect(video).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Attendance readiness" }),
    ).toContainText("1 live · 1 analyzing");
    await page.getByRole("button", { name: "Manage Laptop camera" }).click();
    await page.getByText("Advanced camera settings", { exact: true }).click();
    await expect(
      page.getByText(/Camera \d+ × \d+ · Analysis \d+ × \d+/),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("link", { name: "People", exact: true }).click();
    await expect(
      page.getByText("Laptop camera is sharing", { exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Live Monitor", exact: true }).click();
    await expect(video).toBeVisible();
    await expect
      .poll(() =>
        video.evaluate((player: HTMLVideoElement) => player.currentTime),
      )
      .toBeGreaterThan(0);
    await page
      .getByRole("button", { name: "Detection boxes for Laptop camera" })
      .click();
    const analyzed = page.getByAltText(
      "Analyzed live frame from Laptop camera",
    );
    await expect
      .poll(() =>
        analyzed.evaluate((image: HTMLImageElement) => image.naturalWidth),
      )
      .toBeGreaterThan(0);
    await page
      .getByRole("button", { name: "Detection boxes for Laptop camera" })
      .click();
    await expect(video).toBeVisible();
    await page.screenshot({
      path: "test-results/laptop-camera-live.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Manage Laptop camera" }).click();
    const dialog = page.getByRole("dialog");
    const mode = dialog.getByLabel("Operating mode");
    const apply = dialog.getByRole("button", { name: "Apply mode" });
    await mode.selectOption("setup");
    await apply.click();
    await expect(dialog.getByText("Frames with a usable face")).toBeVisible();
    await expect(dialog.getByText(/attendance paused/)).toBeVisible();
    expect(
      await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth),
    ).toBeTruthy();
    const audit = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(audit.violations.map((item) => item.id)).toEqual([]);
    await page.screenshot({ path: "test-results/camera-setup-phone.png" });
    await mode.selectOption("preview");
    await apply.click();
    await expect(dialog.getByText(/analysis paused/)).toBeVisible();
    await dialog
      .getByRole("button", { name: "Close", exact: true })
      .last()
      .click();
    await expect(video).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Detection boxes for Laptop camera" }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "Manage Laptop camera" }).click();
    await mode.selectOption("live");
    await apply.click();
    await expect(
      dialog.getByText("Automatic check-in", { exact: true }),
    ).toHaveCount(2);
    await dialog
      .getByRole("button", { name: "Close", exact: true })
      .last()
      .click();
    await page
      .getByRole("button", { name: "Stop sharing", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Share again", exact: true }),
    ).toBeVisible();
    await expect(video).toHaveCount(0);
  });
});

test("unknown camera face can be named after human verification", async ({
  page,
}) => {
  await page.request.post("/api/login", {
    data: { password: "test-ui-password-12345" },
  });
  const sample = readFileSync("tests/fixtures/face.jpg").toString("base64");
  let named = false;
  let combined = false;
  const group = (id: string, count: number) => ({
    id,
    sample,
    samples: Array<string>(count).fill(sample),
    sightings: 3,
    first_seen: new Date().toISOString(),
    last_seen: new Date().toISOString(),
  });
  await page.route("**/api/review?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: named
          ? []
          : combined
            ? [group("unknown-test", 5)]
            : [group("unknown-test", 3), group("other-view", 3)],
        total: named ? 0 : combined ? 1 : 2,
        page: 1,
      }),
    }),
  );
  await page.route("**/api/review/unknown-test/combine", async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      source_id: "other-view",
      verified: true,
    });
    combined = true;
    await route.fulfill({ json: group("unknown-test", 5) });
  });
  await page.route("**/api/review/unknown-test/name", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.name).toBe("Ahmad");
    expect(body.consent).toBe(true);
    expect(body.verified).toBe(true);
    expect(body.attendance_enabled).toBe(false);
    named = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "known-test", name: "Ahmad", templates: 3 }),
    });
  });
  await page.goto("/#review");
  await expect(
    page.getByRole("heading", { name: "Name person #unknow" }),
  ).toBeVisible();
  await expect(page.getByAltText(/Identity verification sample/)).toHaveCount(
    3,
  );
  await page.setViewportSize({ width: 360, height: 700 });
  await page.getByText("More review actions", { exact: true }).click();
  await page.getByRole("button", { name: "Combine unnamed groups" }).click();
  const dialog = page.getByRole("dialog", { name: "Combine unnamed groups" });
  await dialog
    .getByLabel("Other group on this page")
    .selectOption("other-view");
  await expect(
    dialog.getByRole("region", { name: "Other face group" }).getByRole("img"),
  ).toHaveCount(3);
  await expect(
    dialog.getByRole("button", { name: "Combine groups", exact: true }),
  ).toBeDisabled();
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBeTruthy();
  await dialog.getByRole("checkbox").check();
  await dialog
    .getByRole("button", { name: "Combine groups", exact: true })
    .click();
  await expect(dialog).toBeHidden();
  await expect(page.getByAltText(/Identity verification sample/)).toHaveCount(
    5,
  );
  await page.getByLabel("Full name", { exact: true }).fill("Ahmad");
  await page.getByLabel("ID number").fill("EMP-001");
  await page.getByLabel("Group / class").fill("Engineering");
  await page.getByLabel("Enable attendance for this person").uncheck();
  await page
    .getByText("This person explicitly consented to biometric attendance.")
    .click();
  await page
    .getByText(
      "I reviewed every sample and verified this person's identity in person.",
    )
    .click();
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();
  expect(
    accessibility.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => n.target),
    })),
  ).toEqual([]);
  await page.getByRole("button", { name: "Save person", exact: true }).click();
  await expect(
    page.getByRole("status").getByText("Ahmad is now known to your cameras."),
  ).toBeVisible();
});

test("real enrollment, manual attendance, CSV export and forget person", async ({
  page,
}) => {
  await page.request.post("/api/login", {
    data: { password: "test-ui-password-12345" },
  });
  await page.goto("/#people");
  await expect(page.getByText("Make your first introduction")).toBeVisible();
  await page
    .getByRole("button", { name: "Enroll a person", exact: true })
    .first()
    .click();
  await page
    .getByLabel("Full name", { exact: true })
    .fill("Integration Fixture");
  await page.getByLabel("Employee or student ID").fill("FIXTURE-01");
  await page.getByLabel("Department or group").fill("Test fixtures");
  await page
    .locator("input[type=file]")
    .setInputFiles([
      "tests/fixtures/enroll-0.jpg",
      "tests/fixtures/enroll-1.jpg",
      "tests/fixtures/enroll-2.jpg",
    ]);
  await page
    .getByText("This person has explicitly consented to biometric attendance.")
    .click();
  await page
    .getByText("I have verified this person's identity in person.")
    .click();
  const enrolled = page.waitForResponse(
    (r) => r.url().endsWith("/api/people") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Enroll person", exact: true })
    .click();
  expect((await enrolled).ok()).toBeTruthy();
  const { id: personId } = await (await enrolled).json();
  await expect(
    page.getByRole("button", { name: /Integration Fixture/ }),
  ).toBeVisible();
  await page.route("**/api/cameras", async (route) => {
    await route.fulfill({
      json: {
        items: Array.from({ length: 6 }, (_, index) => ({
          id: `pool-${index + 1}`,
          name: `Pool camera ${index + 1}`,
          location: "Pool entrance",
          source_type: "network",
          role: index === 0 ? "in" : "observe",
          trigger_mode: "appearance",
          state: "running",
          enabled: true,
          analysis_state: "current",
          preview_ready: true,
          processed_fps: 2,
          tracks:
            index === 0
              ? [
                  {
                    track: "fixture-track",
                    state: "candidate",
                    label: "Integration Fixture · needs verification",
                    person_id: personId,
                    manual_kind: "in",
                    message:
                      "Attendance pending. The camera is still checking liveness.",
                  },
                ]
              : [],
        })),
      },
    });
  });
  await page.route("**/api/cameras/*/snapshot?**", (route) =>
    route.fulfill({
      path: "tests/fixtures/enroll-0.jpg",
      contentType: "image/jpeg",
    }),
  );
  await page.getByRole("link", { name: "Live Monitor", exact: true }).click();
  await expect(page.getByRole("article", { name: /Pool camera/ })).toHaveCount(
    6,
  );
  await expect(
    page.getByRole("article", { name: "Pool camera 6" }),
  ).toContainText("Live");
  await page
    .getByRole("button", { name: "Confirm check-in", exact: true })
    .click();
  await expect(page.getByLabel("Person", { exact: true })).toHaveValue(
    personId,
  );
  await expect(page.getByLabel("Attendance event")).toHaveValue("in");
  await page
    .getByLabel("Reason for manual entry")
    .fill("UI integration check with a public test fixture");
  await page
    .getByRole("button", { name: "Record attendance", exact: true })
    .click();
  await page.getByRole("link", { name: "Attendance", exact: true }).click();
  await expect(page.getByRole("table")).toContainText("Integration Fixture");
  await expect(
    page.getByRole("columnheader", { name: "Match score" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Match scores" }).click();
  await expect(
    page.getByRole("columnheader", { name: "Match score" }),
  ).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/^presence-.*\.csv$/);
  await page.getByRole("link", { name: "People", exact: true }).click();
  await page.getByRole("button", { name: /Integration Fixture/ }).click();
  await expect(page.getByText("Biometric consent recorded")).toBeVisible();
  await expect(page.getByText("Extra face checks available")).toBeVisible();
  await page.getByRole("button", { name: "Update face photos" }).click();
  await expect(
    page.getByRole("dialog", {
      name: /Update face photos for Integration Fixture/,
    }),
  ).toBeVisible();
  await page
    .locator("input[type=file]")
    .setInputFiles([
      "tests/fixtures/enroll-0.jpg",
      "tests/fixtures/enroll-1.jpg",
      "tests/fixtures/enroll-2.jpg",
    ]);
  await page
    .getByText("This person has explicitly consented to biometric attendance.")
    .click();
  await page
    .getByText("I have verified this person's identity in person.")
    .click();
  const refreshed = page.waitForResponse((r) =>
    r.url().endsWith("/refresh-faces"),
  );
  await page
    .getByRole("button", { name: "Update face photos", exact: true })
    .click();
  expect((await refreshed).ok()).toBeTruthy();
  await expect(page.getByText("Face templates refreshed.")).toBeVisible();
  await page.getByRole("button", { name: /Integration Fixture/ }).click();
  await page
    .getByRole("button", { name: "Forget person", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Forget person", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /Integration Fixture/ }),
  ).not.toBeVisible();
});
