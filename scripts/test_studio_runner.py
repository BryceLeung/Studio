"""Policy regression tests; these do not launch an agent or execute a stage."""
import json
from pathlib import Path, PureWindowsPath
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'C:/Users/bryce/OneDrive/Books/Parent Like a Millionaire/Marketing/Studio/01-Love Compounds Too'
PROJECT_SUFFIX = 'Books/Parent Like a Millionaire/Marketing/Studio/01-Love Compounds Too'


def action(rules, value):
    """Mirror Kilo's last-match wildcard evaluation, including optional trailing args."""
    if isinstance(rules, str):
        return rules
    result = 'ask'
    for pattern, decision in rules.items():
        optional_args = pattern.endswith(' *')
        expression = re.escape(pattern[:-2] if optional_args else pattern).replace(r'\*', '.*').replace(r'\?', '.')
        if optional_args:
            expression += '( .*)?'
        if re.fullmatch(expression, value, re.S):
            result = decision
    return result


class StudioRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / 'kilo.json').read_text(encoding='utf-8'))
        cls.agent = cls.config['agent']['studio-runner']
        cls.permissions = cls.agent['permission']

    def test_runner_is_selectable_without_changing_the_default_agent(self):
        self.assertEqual(self.agent['mode'], 'all')
        self.assertNotEqual(self.config.get('default_agent'), 'studio-runner')
        self.assertNotIn('model', self.agent)

    def test_common_and_new_nested_python_helpers_are_approved(self):
        for command in (
            'python "./scripts/validation.py"',
            f'python "./scripts/validation.py" --project-root "{PROJECT}"',
            'python "./.agents/skills/studio/2-Production/New Stage/scripts/new.py" --asset-id ASSET-000001',
            'python -B "./scripts/validation.py" --stage 2.3',
            '& "./.venv-tools/Scripts/python.exe" "./scripts/validation.py"',
            'python3 "./scripts/validation.py"',
            'python3 -B "./scripts/validation.py" --stage 2.3',
            '"./.venv-tools/bin/python" "./scripts/validation.py"',
            '"./.venv-tools/bin/python3" "./scripts/validation.py"',
        ):
            with self.subTest(command=command):
                self.assertEqual(action(self.permissions['bash'], command), 'allow')

    def test_arbitrary_code_external_scripts_installs_and_shell_chains_ask(self):
        for command in (
            'python -c "print(1)"', 'python -m pip install something',
            'python "C:/Temp/outside.py"', 'npm install', 'node render.mjs',
            'python3 -c "print(1)"', 'python3 -m pip install something',
            'python3 "/tmp/outside.py"', 'python3 "./../outside.py"',
            'python "./scripts/../outside.py"',
            'python "./scripts/check.py" > output.txt',
            'python "./scripts/check.py"; Remove-Item other.txt',
            'python "./scripts/check.py" | other-command',
            'python "./scripts/check.py"\nother-command',
        ):
            with self.subTest(command=command):
                self.assertEqual(action(self.permissions['bash'], command), 'ask')

    def test_connected_project_edits_allow_both_path_conventions(self):
        relative = '../../OneDrive/Books/Parent Like a Millionaire/Marketing/Studio/01-Love Compounds Too/asset/src/Composition.tsx'
        for value in (f'{PROJECT}/LOG.md', relative, 'Connected Projects/01-Love Compounds Too/LOG.md'):
            with self.subTest(value=value):
                self.assertEqual(action(self.permissions['edit'], value), 'allow')
                self.assertEqual(action(self.permissions['edit'], str(PureWindowsPath(value))), 'allow')

    def test_workflow_and_unrelated_project_edits_ask(self):
        for value in ('kilo.json', '.kilo/agents/other.md', 'scripts/new.py',
                      '.agents/skills/studio/SKILL.md', PROJECT + '-other/LOG.md',
                      'C:/Users/bryce/.config/kilo/kilo.jsonc'):
            with self.subTest(value=value):
                self.assertEqual(action(self.permissions['edit'], value), 'ask')

    def test_external_access_only_covers_the_registered_target(self):
        for value in (PROJECT, f'{PROJECT}/*', f'{PROJECT}/2-Production/*'):
            self.assertEqual(action(self.permissions['external_directory'], value), 'allow')
            self.assertEqual(action(self.permissions['external_directory'], str(PureWindowsPath(value))), 'allow')
        for value in (PROJECT + '-other/*', 'C:/Users/bryce/OneDrive/*',
                      'C:/Users/bryce/.config/kilo/*'):
            self.assertEqual(action(self.permissions['external_directory'], value), 'ask')

    def test_default_agent_does_not_gain_runner_permissions(self):
        self.assertEqual(action(self.config['permission']['bash'],
                                'python "./scripts/validation.py"'), 'ask')
        self.assertEqual(action(self.config['permission']['bash'],
                                'python3 "./scripts/validation.py"'), 'ask')
        self.assertNotIn('edit', self.config['permission'])
        self.assertNotIn('external_directory', self.config['permission'])

    def test_workers_must_select_the_named_runner(self):
        self.assertEqual(action(self.permissions['task'], 'studio-runner'), 'allow')
        self.assertEqual(action(self.permissions['task'], 'general'), 'ask')

    def test_macos_onedrive_locations_preserve_project_scope(self):
        for prefix in ('/Users/bryce/Library/CloudStorage/OneDrive-Personal',
                       '/Users/another/Library/CloudStorage/OneDrive-Company',
                       '/Users/bryce/OneDrive'):
            project = f'{prefix}/{PROJECT_SUFFIX}'
            with self.subTest(project=project):
                self.assertEqual(action(self.permissions['external_directory'], project), 'allow')
                self.assertEqual(action(self.permissions['external_directory'], project + '/*'), 'allow')
                self.assertEqual(action(self.permissions['edit'], project + '/LOG.md'), 'allow')
                self.assertEqual(action(self.permissions['external_directory'], prefix + '/*'), 'ask')
                self.assertEqual(action(self.permissions['external_directory'], project + '-other/*'), 'ask')
                self.assertEqual(action(self.permissions['edit'], project + '-other/LOG.md'), 'ask')
        for relative in (f'../../Library/CloudStorage/OneDrive-Personal/{PROJECT_SUFFIX}/LOG.md',
                         f'../../../OneDrive/{PROJECT_SUFFIX}/LOG.md'):
            self.assertEqual(action(self.permissions['edit'], relative), 'allow')

    def test_runner_requires_repository_workdir_without_a_windows_checkout_path(self):
        self.assertIn('workdir', self.agent['prompt'])
        self.assertNotIn('C:/Users/bryce/VSCode/Studio', self.agent['prompt'])
        self.assertFalse(any('C:/Users/bryce/VSCode/Studio' in pattern
                             for pattern in self.permissions['bash']))

    def test_three_column_conversion_skill_routes_to_existing_writer_without_stage_run(self):
        stage = ROOT / '.agents/skills/studio/1-Pre Production/1-Script Review'
        skill = stage / 'convert-script-to-3-col-format/SKILL.md'
        text = skill.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('---\nname: studio-convert-script-to-3-col-format\n'))
        self.assertIn('# Convert Script to 3-Col Format', text)
        self.assertIn('convert script to 3-col format', text)
        self.assertIn('--to three-column', text)
        self.assertIn('--output', text)
        self.assertIn('write-script.py', text)
        self.assertIn('Do not invoke `studio run 1.1`', text)
        self.assertIn('this action does not implicitly create an Agent Manager session', text)
        self.assertIn('never in approval or archive directories', text)
        self.assertIn('write or record `DONE`, invalidate completion', text)
        self.assertTrue((skill.parent / '../../../SKILL.md').resolve().is_file())
        self.assertEqual((skill.parent / '../SKILL.md').resolve(), stage / 'SKILL.md')
        self.assertTrue((stage / 'scripts/convert-script.py').is_file())
        self.assertIn('convert-script-to-3-col-format/SKILL.md',
                      (stage / 'SKILL.md').read_text(encoding='utf-8'))
        root_skill = (ROOT / '.agents/skills/studio/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('`convert script to 3-col format [script] [project]`', root_skill)
        self.assertIn('1-Script%20Review/convert-script-to-3-col-format/SKILL.md', root_skill)
        definition = json.loads((stage / 'dependencies.json').read_text(encoding='utf-8'))
        self.assertIn(skill.relative_to(ROOT).as_posix(), definition['workflow_dependencies'])


if __name__ == '__main__':
    unittest.main()
