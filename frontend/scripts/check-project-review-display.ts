/** Run in frontend: npx tsx --tsconfig tsconfig.app.json scripts/check-project-review-display.ts */
import { projectReviewView } from '../src/api/projectReviewDisplay'

function check(condition: unknown, label: string): asserts condition {
  if (!condition) throw new Error(label)
}

const project = {
  project_name: 'PDF원본프로젝트A',
  customer_name: '기관A',
  start_date: '2020.02',
  end_date: '2021.03',
  responsibilities: '수행역할: PL',
  source_refs: [{ document_id: 'doc1', page_no: 1, quote_text: '원문' }],
}
const core = { projects: [project] }
const root = {
  candidate_path: 'projects[0]',
  field_name: null,
  new_value: project,
  old_value: null,
}

const actual = projectReviewView(root, core)
check(actual.projectName === 'PDF원본프로젝트A', 'native title')
check(actual.customerName === '기관A', 'native customer')
check(actual.period === '2020.02 ~ 2021.03', 'native period')
check(actual.role === '수행역할: PL', 'literal role')
check(actual.existingSummary === '—', 'missing old project')
check(actual.updateDescription === null, 'root has no child change')
check(!JSON.stringify(actual).includes('quote_text'), 'no opaque refs in summary')

const rootReview = projectReviewView({
  ...root,
  old_value: [{
    project_name: 'PDF원본프로젝트A(기존)',
    customer_name: '기관A',
    start_date: '2020.02',
    end_date: '2021.03',
  }],
}, core)
check(rootReview.existingSummary.includes('PDF원본프로젝트A(기존)'), 'existing candidate visible')

const child = projectReviewView({
  candidate_path: 'projects[0].responsibilities',
  field_name: 'responsibilities',
  new_value: '수행역할: PL',
  old_value: null,
}, core)
check(child.projectName === 'PDF원본프로젝트A', 'child falls back to parent project')
check(child.updateDescription === '수행역할: 수행역할: PL', 'child update visible')
check(child.role === '수행역할: PL', 'child role stays visible')

const ongoing = projectReviewView({
  candidate_path: 'projects[0]',
  field_name: null,
  new_value: { ...project, end_date: '현재' },
  old_value: null,
}, core)
check(ongoing.period === '2020.02 ~ 현재', 'current end')

const missing = projectReviewView({
  candidate_path: 'projects[25]',
  field_name: null,
  new_value: null,
  old_value: null,
}, core)
check(missing.projectName === '—', 'missing project safe')

const many = Array.from({ length: 36 }, (_, index) => ({
  project_name: `프로젝트${index}`,
  customer_name: '기관A',
  start_date: '2020.02',
  end_date: '2021.03',
  responsibilities: '수행역할: 개발',
}))
const manyCore = { projects: many }
const views = many.map((candidate, index) => projectReviewView({
  candidate_path: `projects[${index}]`,
  field_name: null,
  new_value: candidate,
  old_value: null,
}, manyCore))
check(views.length === 36, 'all 36 native roots represented')
check(new Set(views.map(v => v.projectName)).size === 36, 'names remain independent')
check(many[0].project_name === '프로젝트0', 'candidate object remains unchanged')

console.log('projectReviewDisplay checks: OK')
