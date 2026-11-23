import { expect, test } from "@playwright/test";

const API_URL = "http://127.0.0.1:8000";
const MAILPIT_URL = "http://127.0.0.1:8025";

test("cockpit RTL toggle and HITL reject/approve flow with live Postgres and Mailpit", async ({
  page,
  request,
}) => {
  await page.goto("/");

  await expect(page.getByText("SadaOps Cockpit")).toBeVisible();
  await expect(page.getByText("Infra Healthy")).toBeVisible({ timeout: 10_000 });

  const main = page.locator("main.cockpit");
  await expect(main).toHaveAttribute("dir", "ltr");
  await page.getByRole("button", { name: "العربية" }).click();
  await expect(main).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("لوحة تحكم صدى أوبس")).toBeVisible();
  await page.getByRole("button", { name: "English" }).click();
  await expect(main).toHaveAttribute("dir", "ltr");

  const rejectTag = `rej-${Date.now()}`;
  const rejectProp = await request.post(`${API_URL}/api/hitl/propose`, {
    data: {
      session_id: "e2e-session",
      tool_name: "send_email",
      payload: {
        to: "ops-leads@example.com",
        subject: `E2E Reject ${rejectTag}`,
        body: "Should be cancelled by operator.",
      },
    },
  });
  expect(rejectProp.ok()).toBeTruthy();
  const rejectAction = await rejectProp.json();

  await page.reload();
  await expect(page.getByTestId(`hitl-card-${rejectAction.action_id}`)).toBeVisible();
  await page.getByTestId(`hitl-reject-${rejectAction.action_id}`).click();
  await expect(page.getByTestId(`hitl-card-${rejectAction.action_id}`)).toHaveCount(0);
  await expect(page.getByTestId("hitl-feedback")).toContainText("Rejected");

  const rejectedMailRes = await request.get(`${MAILPIT_URL}/api/v1/search?query=${rejectTag}`);
  expect(rejectedMailRes.ok()).toBeTruthy();
  const rejectedMailData = await rejectedMailRes.json();
  expect(rejectedMailData.messages ?? []).toHaveLength(0);

  // Inline edit must re-lock the SHA-256 approval hash before Mailpit delivery succeeds.
  const approveTag = `app-${Date.now()}`;
  const approveProp = await request.post(`${API_URL}/api/hitl/propose`, {
    data: {
      session_id: "e2e-session",
      tool_name: "send_email",
      payload: {
        to: "wrong@example.com",
        subject: `Typo ${approveTag}`,
        body: "Before inline edit.",
      },
    },
  });
  expect(approveProp.ok()).toBeTruthy();
  const approveAction = await approveProp.json();

  await page.reload();
  await expect(page.getByTestId(`hitl-card-${approveAction.action_id}`)).toBeVisible();
  const initialHashText = await page.getByTestId(`hitl-hash-${approveAction.action_id}`).innerText();

  await page.getByTestId(`hitl-edit-${approveAction.action_id}`).click();
  await page.getByTestId(`hitl-input-${approveAction.action_id}-to`).fill("ops-leads@example.com");
  await page
    .getByTestId(`hitl-input-${approveAction.action_id}-subject`)
    .fill(`E2E Edited ${approveTag}`);
  await page.getByTestId(`hitl-save-${approveAction.action_id}`).click();
  await expect(page.getByTestId("hitl-feedback")).toContainText("Re-locked");
  const updatedHashText = await page.getByTestId(`hitl-hash-${approveAction.action_id}`).innerText();
  expect(updatedHashText).not.toEqual(initialHashText);

  await page.getByTestId(`hitl-approve-${approveAction.action_id}`).click();
  await expect(page.getByTestId(`hitl-card-${approveAction.action_id}`)).toHaveCount(0);
  await expect(page.getByTestId("hitl-feedback")).toContainText("Executed");

  const approvedMailRes = await request.get(`${MAILPIT_URL}/api/v1/search?query=${approveTag}`);
  expect(approvedMailRes.ok()).toBeTruthy();
  const approvedMailData = await approvedMailRes.json();
  expect(approvedMailData.messages).toHaveLength(1);
  expect(approvedMailData.messages[0].Subject).toContain(`E2E Edited ${approveTag}`);
  expect(approvedMailData.messages[0].To[0].Address).toBe("ops-leads@example.com");
});
