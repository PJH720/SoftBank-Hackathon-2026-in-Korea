import { test, expect } from '@playwright/test';
import { setupOps } from './fixtures/ops.js';

test('default model selection preserves the existing deployment payload', async ({ page }) => {
  const sent = await setupOps(page);
  await page.getByRole('button', { name: '새 배포', exact: true }).click();
  await expect(page.getByLabel('자가치유 AI', { exact: true })).toHaveValue('server');
  await page.getByRole('button', { name: /One Action Deploy/ }).click();
  await expect.poll(() => sent.deploys.length).toBe(1);
  expect(sent.deploys[0]).not.toHaveProperty('llm_config');
});

for (const provider of ['anthropic', 'openai']) {
  test(`${provider} selection sends a trimmed model override`, async ({ page }) => {
    const sent = await setupOps(page);
    await page.getByRole('button', { name: '새 배포', exact: true }).click();
    await page.getByLabel('자가치유 AI', { exact: true }).selectOption(provider);
    await page.getByLabel(/모델명/).fill(' selected-model ');
    await page.getByRole('button', { name: /One Action Deploy/ }).click();
    await expect.poll(() => sent.deploys.length).toBe(1);
    expect(sent.deploys[0].llm_config).toEqual({ provider, model: 'selected-model' });
  });
}

test('invalid local configuration blocks submit and valid local configuration reaches the API', async ({ page }) => {
  const sent = await setupOps(page);
  await page.getByRole('button', { name: '새 배포', exact: true }).click();
  await page.getByLabel('자가치유 AI', { exact: true }).selectOption('local');
  const submit = page.getByRole('button', { name: /One Action Deploy/ });
  await expect(submit).toBeDisabled();
  await page.getByLabel(/모델명/).fill(' qwen ');
  await page.getByLabel('로컬 AI 서버 URL (필수)').fill('ftp://localhost/v1');
  await expect(submit).toBeDisabled();
  await expect(page.getByRole('alert')).toContainText('HTTP');
  expect(sent.deploys).toHaveLength(0);
  await page.getByLabel('로컬 AI 서버 URL (필수)').fill('http://localhost:11434/v1');
  await submit.click();
  await expect.poll(() => sent.deploys.length).toBe(1);
  expect(sent.deploys[0].llm_config).toEqual({ provider: 'local', model: 'qwen', base_url: 'http://localhost:11434/v1' });
});

test('switching to a hosted provider clears the local endpoint', async ({ page }) => {
  await setupOps(page);
  await page.getByRole('button', { name: '새 배포', exact: true }).click();
  const provider = page.getByLabel('자가치유 AI', { exact: true });
  await provider.selectOption('local');
  await page.getByLabel('로컬 AI 서버 URL (필수)').fill('https://local.example/v1');
  await provider.selectOption('openai');
  await expect(page.getByLabel('로컬 AI 서버 URL (필수)')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /One Action Deploy/ })).toBeEnabled();
  await provider.selectOption('local');
  await expect(page.getByLabel('로컬 AI 서버 URL (필수)')).toHaveValue('');
});
