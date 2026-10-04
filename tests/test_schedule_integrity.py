import tempfile
import unittest
from flask import template_rendered
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.db import get_db
from app.scheduler import capacity_on, replan


class ScheduleIntegrityTest(unittest.TestCase):
    """Exercise real edit endpoints and check the resulting calendar, not just dates."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.today = date(2026, 10, 4)
        for module in ('app.routes', 'app.scheduler'):
            clock = patch(f'{module}.local_today', return_value=self.today)
            clock.start()
            self.addCleanup(clock.stop)
        self.app = create_app({'TESTING': True, 'DATABASE': str(root / 'journal.sqlite3'),
                               'BACKUP_DIR': str(root / 'backups')})
        self.client = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE user_capacity_profiles SET hours_json='[4,4,4,4,4,4,4]' WHERE user_id=1")
            db.execute("INSERT INTO courses (name) VALUES ('Khóa kiểm tra')")
            db.execute('INSERT INTO plan_courses VALUES (1,1,1)')
            self.tasks = []
            for number, hours in enumerate((3.5, 4.5, 2), 1):
                lesson = db.execute('''INSERT INTO lectures
                    (course_id,lecture_number,title,estimated_hours,remaining_hours)
                    VALUES (1,?,?,?,?)''', (number, f'Bài {number}', hours, hours)).lastrowid
                task = db.execute('''INSERT INTO lecture_tasks
                    (lecture_id,position,kind,title,estimated_hours,remaining_hours)
                    VALUES (?,1,'video',?,?,?)''', (lesson, f'Phần {number}', hours, hours)).lastrowid
                self.tasks.append(task)
            replan(self.today)
            db.commit()

    def rows(self, sql, *args):
        with self.app.app_context():
            return [dict(row) for row in get_db().execute(sql, args)]

    def post(self, path, data):
        response = self.client.post(path, data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('class="notice error"', response.get_data(as_text=True))
        return response

    def assert_consistent(self):
        with self.app.app_context():
            db = get_db()
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
            for task in db.execute("SELECT * FROM lecture_tasks WHERE status!='Hoàn thành'"):
                planned = db.execute('''SELECT SUM(hours),MAX(study_date) FROM schedule_allocations
                    WHERE task_id=?''', (task['id'],)).fetchone()
                self.assertAlmostEqual(planned[0] or 0, task['remaining_hours'])
                if not task['manual_deadline']:
                    self.assertEqual(planned[1], task['deadline'])
            for lesson in db.execute('SELECT * FROM lectures'):
                parts = db.execute('SELECT * FROM lecture_tasks WHERE lecture_id=?', (lesson['id'],)).fetchall()
                if parts:
                    self.assertAlmostEqual(lesson['estimated_hours'], sum(t['estimated_hours'] for t in parts))
                    self.assertAlmostEqual(lesson['remaining_hours'], sum(t['remaining_hours'] for t in parts))
                    open_dates = [t['deadline'] for t in parts if t['status']!='Hoàn thành' and t['deadline']]
                    if open_dates and not lesson['manual_deadline']:
                        self.assertEqual(lesson['deadline'], max(open_dates))
            days = db.execute('SELECT study_date,SUM(hours) hours FROM schedule_allocations GROUP BY study_date').fetchall()
            for day in days:
                self.assertLessEqual(day['hours'], capacity_on(date.fromisoformat(day['study_date'])) + .001)
            finish = db.execute('SELECT MAX(study_date) FROM schedule_allocations').fetchone()[0]
            self.assertEqual(db.execute('SELECT target_date FROM courses WHERE id=1').fetchone()[0], finish)

    def test_split_insert_add_update_delete_sequence_keeps_hours_and_calendar_consistent(self):
        initial = self.rows('SELECT task_id,study_date,hours FROM schedule_allocations ORDER BY id')
        self.assertEqual([(r['study_date'],r['hours']) for r in initial if r['task_id']==2],
                         [(self.today.isoformat(),.5), ((self.today+timedelta(days=1)).isoformat(),4)])
        self.post('/courses/1/lectures', {'insert_before':'2','lecture_number':'99','title':'Bài ôn tập'})
        inserted = self.rows('SELECT * FROM lectures WHERE lecture_number=2')[0]
        self.assertEqual(inserted['estimated_hours'], 0)
        self.post(f"/lectures/{inserted['id']}/tasks", {'title':'Ôn tập','kind':'review','estimated_hours':'2'})
        self.assert_consistent()
        self.post('/tasks/2', {'title':'Phần 2','kind':'video','estimated_hours':'6',
                              'remaining_hours':'5','change_reason':'Cần luyện tập thêm'})
        self.assert_consistent()
        page = self.client.get('/schedule').get_data(as_text=True)
        self.assertIn('Phần 1/', page)
        self.assertIn('Bài học tương ứng:', page)
        self.post(f"/lectures/{inserted['id']}/delete", {'confirmation':'delete'})
        self.assert_consistent()
        self.assertEqual([r['lecture_number'] for r in self.rows('SELECT lecture_number FROM lectures ORDER BY lecture_number')], [1,2,3])
        self.post('/tasks/3/delete', {'confirmation':'delete'})
        self.assert_consistent()
        self.assertFalse(self.rows('SELECT * FROM schedule_allocations WHERE task_id=3'))

    def test_adding_more_parts_and_deleting_last_part_updates_total(self):
        self.post('/lectures/1/tasks', {'title':'Bài tập mới','kind':'exercises','estimated_hours':'2'})
        self.assert_consistent()
        self.assertEqual(self.rows('SELECT estimated_hours FROM lectures WHERE id=1')[0]['estimated_hours'],5.5)
        added = self.rows('SELECT id FROM lecture_tasks WHERE lecture_id=1 AND position=2')[0]['id']
        self.post(f'/tasks/{added}/delete', {'confirmation':'delete'})
        self.post('/tasks/1/delete', {'confirmation':'delete'})
        self.assertEqual(self.rows('SELECT estimated_hours,remaining_hours FROM lectures WHERE id=1')[0],
                         {'estimated_hours':0,'remaining_hours':0})
        self.assert_consistent()

    def test_history_does_not_prevent_repairing_missing_open_allocations(self):
        with self.app.app_context():
            db=get_db()
            yesterday=(self.today-timedelta(days=1)).isoformat()
            db.execute("UPDATE lecture_tasks SET status='Hoàn thành',remaining_hours=0,completed_at=? WHERE id=1", (yesterday,))
            db.execute("UPDATE lectures SET status='Hoàn thành',remaining_hours=0,completed_at=? WHERE id=1", (yesterday,))
            db.execute('DELETE FROM schedule_allocations')
            db.execute('''INSERT INTO schedule_allocations (course_id,lecture_id,task_id,study_date,hours)
                VALUES (1,1,1,?,3.5)''', (yesterday,))
            db.commit()
        self.client.get('/schedule')
        self.assert_consistent()
        self.assertTrue(self.rows('SELECT * FROM schedule_allocations WHERE study_date=?', self.today.isoformat()))
        history=self.client.get('/schedule',query_string={'date':yesterday}).get_data(as_text=True)
        self.assertIn('Phần 1', history)
        self.assertIn('Hoàn thành', history)

    def test_edit_with_unrelated_overdue_work_still_replans_other_work(self):
        yesterday=(self.today-timedelta(days=1)).isoformat()
        with self.app.app_context():
            db=get_db()
            db.execute("UPDATE lecture_tasks SET deadline=? WHERE id=1", (yesterday,))
            db.execute("UPDATE lectures SET deadline=? WHERE id=1", (yesterday,))
            db.execute('UPDATE schedule_allocations SET study_date=? WHERE task_id=1', (yesterday,))
            db.commit()
        self.client.post('/tasks/2',data={'title':'Phần 2','kind':'video','estimated_hours':'7',
                                        'remaining_hours':'7','change_reason':'Tăng khối lượng'})
        self.assert_consistent()
        self.assertEqual(self.rows('SELECT deadline FROM lecture_tasks WHERE id=1')[0]['deadline'],yesterday)
        self.assertEqual(sum(r['hours'] for r in self.rows('SELECT hours FROM schedule_allocations WHERE task_id=2')),7)

    def test_zero_hour_inherited_lesson_does_not_block_other_edits(self):
        self.post('/courses/1/lectures',{'insert_before':'2','lecture_number':'99','title':'Bài ôn'})
        self.post('/tasks/1',{'title':'Phần 1','estimated_hours':'12','remaining_hours':'12',
                             'change_reason':'Thêm nội dung'})
        self.assert_consistent()

    def test_remote_style_delete_without_fk_cascades_leaves_no_orphans(self):
        @self.app.before_request
        def disable_cascades():
            get_db().execute('PRAGMA foreign_keys=OFF')
        self.post('/lectures/2/delete',{'confirmation':'delete'})
        self.assertFalse(self.rows('SELECT * FROM lecture_tasks WHERE lecture_id=2'))
        self.assertFalse(self.rows('SELECT * FROM schedule_allocations WHERE lecture_id=2'))
        self.assert_consistent()

    def test_postponing_one_plan_keeps_other_plan_on_today_and_persists_after_edit(self):
        with self.app.app_context():
            db=get_db()
            db.execute("INSERT INTO study_plans (name,priority,owner_user_id) VALUES ('Kế hoạch khác',2,1)")
            db.execute("INSERT INTO courses (name) VALUES ('Khóa khác')")
            db.execute('INSERT INTO plan_courses VALUES (2,2,1)')
            db.execute('''INSERT INTO lectures (course_id,lecture_number,title,estimated_hours,remaining_hours)
                VALUES (2,1,'Bài khác',1,1)''')
            db.execute("UPDATE lecture_tasks SET remaining_hours=1,estimated_hours=1 WHERE id=1")
            db.execute("UPDATE lectures SET remaining_hours=1,estimated_hours=1 WHERE id=1")
            replan(self.today)
            db.commit()
        self.post('/tasks/1/miss',{'reason':'Bận hôm nay'})
        self.assertTrue(self.rows('SELECT * FROM schedule_allocations WHERE course_id=2 AND study_date=?',self.today.isoformat()))
        self.assertFalse(self.rows('SELECT * FROM schedule_allocations WHERE task_id=1 AND study_date=?',self.today.isoformat()))
        self.post('/lectures/1/tasks',{'title':'Phần bổ sung','kind':'review','estimated_hours':'1'})
        self.assertFalse(self.rows('SELECT * FROM schedule_allocations WHERE task_id=1 AND study_date=?',self.today.isoformat()))

    def test_fixed_manual_deadline_failure_rolls_back_data_and_calendar_together(self):
        self.post('/lectures/1/deadline',{'deadline':self.today.isoformat(),'reason':'Hạn cố định'})
        before=self.rows('SELECT * FROM schedule_allocations ORDER BY id')
        response=self.client.post('/lectures/1/tasks',data={'title':'Quá nhiều','kind':'review','estimated_hours':'8'},follow_redirects=True)
        self.assertIn('Không thể thêm phần việc',response.get_data(as_text=True))
        self.assertEqual(self.rows('SELECT * FROM schedule_allocations ORDER BY id'),before)
        self.assertFalse(self.rows("SELECT id FROM lecture_tasks WHERE title='Quá nhiều'"))

    def test_recording_one_overdue_reason_keeps_other_overdue_items_visible(self):
        yesterday=(self.today-timedelta(days=1)).isoformat()
        with self.app.app_context():
            db=get_db()
            for task in (1,2):
                db.execute('UPDATE lecture_tasks SET deadline=? WHERE id=?',(yesterday,task))
                db.execute('UPDATE lectures SET deadline=? WHERE id=?',(yesterday,task))
            db.commit()
        self.post('/tasks/1/miss',{'reason':'Cần ôn lại'})
        self.assertTrue(self.rows('SELECT * FROM missed_deadlines WHERE task_id=1 AND new_deadline IS NOT NULL'))
        self.assertEqual(self.rows('SELECT deadline FROM lecture_tasks WHERE id=2')[0]['deadline'],yesterday)
        self.assertIn('Phần việc quá hạn cần xử lý',self.client.get('/schedule').get_data(as_text=True))

    def test_october_fourth_work_is_visible_in_september_calendar_trailing_cells(self):
        contexts=[]
        def capture(sender, template, context, **kwargs):
            contexts.append(context)
        with template_rendered.connected_to(capture,self.app):
            response=self.client.get('/schedule',query_string={'date':'2026-09-30'})
        self.assertEqual(response.status_code,200)
        context=contexts[-1]
        self.assertEqual(context['today'],self.today)
        self.assertEqual(context['day_summaries']['2026-10-04']['hours'],4)
        self.assertEqual(len(context['day_summaries']['2026-10-04']['work_items']),2)
        self.assertIn('Bài 2: Bài 2 · Phần 2',response.get_data(as_text=True))

    def test_delete_course_and_plan_without_fk_cascades_removes_members_and_allocations(self):
        @self.app.before_request
        def disable_cascades():
            get_db().execute('PRAGMA foreign_keys=OFF')
        self.post('/courses/1/delete',{'course_name':'Khóa kiểm tra'})
        for table in ('lectures','lecture_tasks','schedule_allocations','plan_courses'):
            self.assertFalse(self.rows(f'SELECT * FROM {table}'))
        self.post('/plans/1/delete',{'name':'Kế hoạch đầu tiên'})
        self.assertFalse(self.rows('SELECT * FROM study_plans'))

    def test_capacity_change_with_overdue_work_updates_future_allocations(self):
        yesterday=(self.today-timedelta(days=1)).isoformat()
        with self.app.app_context():
            db=get_db()
            db.execute('UPDATE lecture_tasks SET deadline=? WHERE id=1',(yesterday,))
            db.execute('UPDATE lectures SET deadline=? WHERE id=1',(yesterday,))
            db.execute('UPDATE schedule_allocations SET study_date=? WHERE task_id=1',(yesterday,))
            db.commit()
        self.client.post('/schedule/override',data={'study_date':self.today.isoformat(),
                                                   'hours':'0','reason':'Bận cả ngày'})
        self.assertFalse(self.rows('SELECT * FROM schedule_allocations WHERE study_date=?',self.today.isoformat()))
        self.assert_consistent()

    def test_structural_edits_leave_other_users_allocations_unchanged(self):
        with self.app.app_context():
            db=get_db()
            db.execute("INSERT INTO users (id,username) VALUES (2,'other')")
            db.execute("INSERT INTO courses (name,owner_user_id) VALUES ('Khóa riêng',2)")
            lesson=db.execute('''INSERT INTO lectures (course_id,lecture_number,title,estimated_hours,remaining_hours)
                VALUES (2,1,'Bài riêng',2,2)''').lastrowid
            db.execute('''INSERT INTO schedule_allocations (course_id,lecture_id,study_date,hours)
                VALUES (2,?,?,2)''',(lesson,self.today.isoformat()))
            db.commit()
        before=self.rows('SELECT * FROM schedule_allocations WHERE course_id=2')
        self.post('/courses/1/lectures',{'insert_before':'2','lecture_number':'99','title':'Bài chèn'})
        inserted=self.rows('SELECT id FROM lectures WHERE course_id=1 AND lecture_number=2')[0]['id']
        self.post(f'/lectures/{inserted}/tasks',{'title':'Việc chèn','kind':'review','estimated_hours':'3'})
        self.post(f'/lectures/{inserted}/delete',{'confirmation':'delete'})
        self.assertEqual(self.rows('SELECT * FROM schedule_allocations WHERE course_id=2'),before)

    def test_unschedulable_capacity_change_rolls_back_profile_and_allocations(self):
        before=self.rows('SELECT * FROM schedule_allocations ORDER BY id')
        profiles=self.rows('SELECT * FROM user_capacity_profiles ORDER BY id')
        form={'effective_from':self.today.isoformat(),'reason':'Không còn giờ học'}
        form.update({f'day_{i}':'0' for i in range(7)})
        response=self.client.post('/schedule/capacity',data=form,follow_redirects=True)
        self.assertIn('Không thể lập lịch',response.get_data(as_text=True))
        self.assertEqual(self.rows('SELECT * FROM schedule_allocations ORDER BY id'),before)
        self.assertEqual(self.rows('SELECT * FROM user_capacity_profiles ORDER BY id'),profiles)

    def test_completed_today_work_stays_in_history_and_consumes_capacity_once(self):
        self.post('/tasks/1/complete',{'completed_at':self.today.isoformat()})
        self.assert_consistent()
        allocations=self.rows('SELECT * FROM schedule_allocations WHERE task_id=1')
        self.assertEqual(sum(r['hours'] for r in allocations),3.5)
        page=self.client.get('/schedule').get_data(as_text=True)
        self.assertIn('Phần 1',page)
        self.assertIn('Hoàn thành',page)
        self.assertEqual(sum(r['hours'] for r in self.rows('SELECT hours FROM schedule_allocations WHERE study_date=?',self.today.isoformat())),4)

    def test_backdating_one_course_start_does_not_backdate_another_course(self):
        with self.app.app_context():
            db=get_db()
            db.execute("INSERT INTO courses (name) VALUES ('Khóa kế tiếp')")
            db.execute('INSERT INTO plan_courses VALUES (1,2,2)')
            db.execute('''INSERT INTO lectures (course_id,lecture_number,title,estimated_hours,remaining_hours)
                VALUES (2,1,'Bài kế tiếp',2,2)''')
            db.commit()
        self.client.post('/courses/1/start-date',data={'start_date':'2026-09-01'})
        allocations=self.rows('SELECT study_date FROM schedule_allocations WHERE course_id=2')
        self.assertTrue(allocations)
        self.assertTrue(all(row['study_date']>=self.today.isoformat() for row in allocations))


if __name__=='__main__':
    unittest.main()
