from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from app.services.codex_runner import CodexRunner
from app.services.workshop_recovery import Failure, WorkshopGenerationError, generate_with_recovery
from app.services.workshop_runner import WorkshopImagegen, WorkshopProcessor

OVERLOAD={'message':'Selected model is at capacity. Please try a different model.', 'codexErrorInfo':'serverOverloaded'}
JOB={'model_config':{'model':'gpt-6.1-sol','effort':'xhigh'}}


class WorkshopRecoveryTests(unittest.TestCase):
    def recover(self, effects, *, cancel=False):
        generator=Mock();generator.generate.side_effect=effects
        attempts=[];progress=Mock();clock=[0]
        def wait(seconds):clock[0]+=seconds;return cancel
        with patch('app.services.workshop_recovery.time.monotonic',side_effect=lambda:clock[0]),patch('app.services.workshop_recovery.random.uniform',return_value=0):
            try:
                result=generate_with_recovery(generator,JOB,Path('.'),[],progress=progress,cancelled=lambda:False,wait=wait,attempts=attempts)
                return result,generator,attempts,progress
            except Exception as exc:
                exc.test_attempts=attempts;exc.test_generator=generator
                raise

    def test_overload_not_quota_and_formatted_error_is_idempotent(self):
        f=Failure.from_error(OVERLOAD);self.assertEqual(f.category,'overloaded');self.assertTrue(f.transient)
        once=CodexRunner._failure_message(OVERLOAD);twice=CodexRunner._failure_message({'message':once})
        self.assertEqual(once,twice);self.assertEqual(twice.count('上游详情：'),1);self.assertNotIn('unknown',twice)
        self.assertEqual(Failure.from_error({'message':twice}).category,'overloaded')

    def test_quota_auth_and_invalid_input_not_retried(self):
        for payload,category in [({'message':'insufficient_quota at capacity'},'quota'),({'message':'unauthorized'},'authentication'),({'message':'badRequest'},'unknown')]:
            with self.subTest(category=category):
                f=Failure.from_error(payload);self.assertEqual(f.category,category);self.assertFalse(f.transient)
                with self.assertRaises(WorkshopGenerationError) as caught:self.recover([WorkshopGenerationError(payload)])
                self.assertEqual(caught.exception.test_generator.generate.call_count,1)

    def test_retry_same_frozen_model_and_audit_all_attempts(self):
        result,generator,attempts,progress=self.recover([WorkshopGenerationError(OVERLOAD,thread_id='old'),{'thread_id':'new'}])
        self.assertEqual(generator.generate.call_count,2);self.assertEqual(len(result['attempts']),2)
        self.assertEqual(attempts[0]['category'],'overloaded');self.assertEqual(attempts[1]['status'],'completed')
        self.assertTrue(all(call.args[0] is JOB for call in generator.generate.call_args_list))
        self.assertTrue(any('模型保持不变' in call.args[0] for call in progress.call_args_list))

    def test_bounded_attempts(self):
        with self.assertRaises(WorkshopGenerationError) as caught:self.recover([WorkshopGenerationError(OVERLOAD)]*3)
        self.assertEqual(len(caught.exception.test_attempts),3);self.assertEqual(caught.exception.test_generator.generate.call_count,3)

    def test_cancel_backoff_and_do_not_retry_started_image_work(self):
        with self.assertRaisesRegex(RuntimeError,'停止'):self.recover([WorkshopGenerationError(OVERLOAD)],cancel=True)
        for options in [{'image_requested':True},{'images':[b'partial']}]:
            with self.subTest(options=options):
                with self.assertRaises(WorkshopGenerationError) as caught:self.recover([WorkshopGenerationError(OVERLOAD,**options)])
                self.assertEqual(caught.exception.test_generator.generate.call_count,1)

    def test_numeric_retry_after_and_nested_error_code(self):
        f=Failure.from_error({'message':'busy','codexErrorInfo':{'serverOverloaded':{'httpStatusCode':503,'retryAfter':65}}})
        self.assertEqual(f.category,'overloaded');self.assertEqual(f.retry_after,65)
        self.assertEqual(Failure.from_error({'message':'overloaded','retryAfter':'not trusted'}).retry_after,0)

    def test_generator_keeps_partial_native_state_on_transport_failure(self):
        generator=WorkshopImagegen()
        def failed(*args,state,**kwargs):
            state.update(thread_id='thread',outputs=[b'image'],calls=[{'id':'call'}],image_requested=True)
            raise RuntimeError('StreamDisconnected')
        with patch.object(generator,'_generate',side_effect=failed),self.assertRaises(WorkshopGenerationError) as caught:
            generator.generate(JOB,Path('.'),[],progress=Mock(),cancelled=lambda:False)
        error=caught.exception
        self.assertEqual(error.failure.category,'connection');self.assertFalse(error.retryable)
        self.assertEqual(error.images,[b'image']);self.assertEqual(error.thread_id,'thread')

    def test_completed_design_image_survives_later_text_overload_without_regeneration(self):
        generator=WorkshopImagegen();progress=Mock()
        job={**JOB,'kind':'design','base_theme':{'tokens':{'skin-bg':'#FFFFFF'},'scenePalette':{'leaf':'#123456'}}}
        def failed(*args,state,**kwargs):
            state.update(thread_id='thread',outputs=[b'native-image'],calls=[{'id':'native-call'}],image_requested=True)
            raise WorkshopGenerationError(OVERLOAD,thread_id='thread',images=state['outputs'],calls=state['calls'],image_requested=True)
        with patch.object(generator,'_generate',side_effect=failed) as generate:
            result=generator.generate(job,Path('.'),[],progress=progress,cancelled=lambda:False)
        self.assertEqual(generate.call_count,1);self.assertTrue(result['imagegen_verified'])
        self.assertEqual(result['images'],[b'native-image']);self.assertEqual(result['summary_warning']['category'],'overloaded')
        self.assertIn('暂沿用基础主题配色',result['answer'])

    def test_image_preservation_does_not_complete_production_quota_or_cancelled_work(self):
        for kind,error,cancelled in [('production',OVERLOAD,False),('design',{'message':'insufficient_quota'},False),('design',OVERLOAD,True)]:
            with self.subTest(kind=kind,error=error,cancelled=cancelled):
                exception=WorkshopGenerationError(error,images=[b'image'],calls=[{'id':'call'}],image_requested=True)
                with patch.object(WorkshopImagegen,'_generate',side_effect=exception),self.assertRaises(WorkshopGenerationError):
                    WorkshopImagegen().generate({**JOB,'kind':kind},Path('.'),[],progress=Mock(),cancelled=lambda:cancelled)

    def test_processor_keeps_terminal_thread_partial_assets_and_single_error(self):
        store=Mock();store.workshop_upload.return_value={'id':'partial'}
        base={'preview':'/static/appearance/mint-garden.png'}
        job={**JOB,'kind':'design','design_id':'recovery-test','input':{},'revisions':[],'base_theme':base}
        def fail(*args,progress,**kwargs):
            progress('已生成图像','native-thread')
            raise WorkshopGenerationError(OVERLOAD,thread_id='native-thread',images=[b'image'],calls=[{'id':'one'}],image_requested=True)
        with patch('app.services.workshop_runner.generate_with_recovery',side_effect=fail),patch.object(Path,'mkdir'):
            WorkshopProcessor(store).run(job,threading.Event())
        final=store.workshop_update.call_args.kwargs
        self.assertEqual(final['status'],'failed');self.assertEqual(final['thread_id'],'native-thread')
        self.assertEqual(final['result']['partial_assets'],['partial']);self.assertEqual(final['error_message'].count('上游详情：'),1)


if __name__=='__main__':unittest.main()
