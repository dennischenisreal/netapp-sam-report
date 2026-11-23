"""
export_rule_table.xml 解析模块
提取 Export Policy 及其规则(vserver / policy / rule / protocol / clientmatch)
"""
import re


def parse_export_rules(xml_content: str) -> list:
    """解析 export_rule_table.xml，返回 Export Policy 规则列表"""
    rules = []
    try:
        content = re.sub(r'xmlns(:[^=]*)?="[^"]*"', '', xml_content)
        rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
        if not rows:
            rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)

        for row in rows:
            def get_field(tag):
                m = re.search(rf'<{tag}(?:\s[^>]*)?>(.*?)</{tag}>', row, re.DOTALL)
                return m.group(1).strip() if m else ''

            def get_list(tag):
                """protocol / rorule / rwrule / superuser 为 asup:list 结构"""
                block = get_field(tag)
                items = re.findall(r'<asup:li>(.*?)</asup:li>', block, re.DOTALL)
                if items:
                    return ', '.join(x.strip() for x in items if x.strip())
                return block

            vserver = get_field('vserver')
            policy_name = get_field('policyname')
            if not vserver or not policy_name:
                continue

            rules.append({
                'vserver': vserver,
                'policy': policy_name,
                'rule_index': get_field('ruleindex'),
                'protocol': get_list('protocol'),
                'client_match': get_field('clientmatch'),
                'ro_rule': get_list('rorule'),
                'rw_rule': get_list('rwrule'),
                'anon': get_field('anon'),
                'superuser': get_list('superuser'),
                'allow_suid': get_field('allow_suid'),
                'allow_dev': get_field('allow_dev'),
            })
    except Exception as e:
        print(f"解析 export_rule_table.xml 时出错: {e}")
    return rules
