import { describe, expect, it } from 'vitest'
import { emptyRun } from '../lib/run'
import { josa } from './copy'
import { explainFailure } from './failure'
import { progress, sentence } from './progress'

describe('josa: particles follow the last sound', () => {
  it('handles Hangul, words, acronyms and parentheses', () => {
    expect(josa('Cloud Run', '을', '를')).toBe('Cloud Run을')
    expect(josa('내 컴퓨터', '을', '를')).toBe('내 컴퓨터를')
    expect(josa('DB', '이', '가')).toBe('DB가') // 디비
    expect(josa('PostgreSQL', '을', '를')).toBe('PostgreSQL을') // 에스큐엘
    expect(josa('Docker', '을', '를')).toBe('Docker를')
    expect(josa('clef-flash (로컬·Jev 호환)', '이', '가')).toBe('clef-flash (로컬·Jev 호환)가')
    expect(josa('파일', '으로', '로')).toBe('파일로') // ㄹ takes 로
    expect(josa('Cloud Run', '으로', '로')).toBe('Cloud Run으로')
  })
})

describe('explainFailure: what to fix, in plain words', () => {
  it('turns preflight problems into steps', () => {
    const stderr = '[deployer] stage=preflight kind=user_action_required target=cloudrun\n- gcloud CLI not installed (brew install --cask gcloud-cli)\n- API not enabled: run.googleapis.com (gcloud services enable run.googleapis.com)'
    const why = explainFailure('Not auto-healable: deployer reported kind=user_action_required at stage=preflight', stderr)
    expect(why.title).toBe('올리기 전에 준비가 필요해요')
    expect(why.lines[0]).toContain('구글 클라우드 도구(gcloud)가 없어요')
    expect(why.lines[1]).toContain('gcloud services enable run.googleapis.com')
  })

  it('explains healer outcomes and keeps unknown messages', () => {
    expect(explainFailure('Not auto-healable: application code error at app.py:3').title).toContain('앱 코드 안에서 오류')
    expect(explainFailure('Stopped: the source fix is up as a PR (https://x/pull/1).').title).toContain('PR로 올렸어요')
    expect(explainFailure('something new').title).toBe('something new')
  })
})

describe('progress: five plain steps', () => {
  it('asks, builds and finishes in words', () => {
    const run = { ...emptyRun('d1', { targets: [] }), decision: { service: 'vm' }, envRequest: { items: [] } }
    expect(progress(run)).toMatchObject({ read: 'done', pick: 'waiting' })
    expect(sentence(run)).toBe('배포 전에 몇 가지만 여쭤볼게요.')
    run.envRequest = null
    run.targetState = { local: { phase: 'deploying' } }
    expect(sentence(run)).toBe('앱을 만들어 올리고 있어요. 보통 1분 안에 끝나요.')
    run.targetState = { local: { phase: 'done' } }
    run.status = 'completed'
    expect(progress(run).done).toBe('done')
    expect(sentence(run)).toBe('배포를 마쳤어요. 바로 열어 볼 수 있어요.')
  })
})
