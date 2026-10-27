# تقرير المشروع

## نظرة عامة

سطح مكتب Ubuntu 24.04 كامل يعمل داخل حاوية ويُعرض في المتصفح عبر noVNC.
الواجهة خفيفة من طرفين: جلسة XFCE بلا مؤثرات وبلا أيقونات على الخلفية،
وسطح المكتب يُبَثّ بـ TigerVNC + noVNC خلف nginx — بلا KasmVNC وبلا طبقة
محاكاة systemd داخل الحاوية.

## المكوّنات

| المكوّن | الدور |
|---|---|
| Ubuntu 24.04 | نظام الأساس |
| XFCE 4 | جلسة سطح المكتب (بلا `xfce4-session` وبلا `xfdesktop`) |
| TigerVNC | خادم X (عملية واحدة، RFB على loopback) |
| noVNC + websockify | العميل في المتصفح + جسر WebSocket |
| nginx | المنفذ العام الوحيد: مصادقة + صفحة الحالة + تمرير WebSocket |
| Google Chrome | المتصفح الافتراضي |
| supervisord | PID 1 داخل الحاوية — سبع خدمات فقط |
| `control-server.py` | `/api/status` و`/healthz` على loopback |

## ما تمت إزالته

- **Tailscale**: الحزمة، الخدمة، سكربتاتها، وحداتها، وكل واجهتها في صفحة
  التحكم. لم يبق أي أثر.
- **Hermes / DeepSeek**: التثبيت، سكربت الإقلاع، برنامج Supervisor،
  والمتغيّر `DEEPSEEK_API_KEY`. لم يبق أي أثر.
- **KasmVNC**: بُني الحاوية عليه سابقاً. أُزيل لأنه يستهلك وحدة المعالجة في
  الترميز المتكيّف، وكان يمرّ عبر reverse proxy بايثون كل حركة الشبكة.
- **طبقة توافق systemd**: أربع خدمات بايثون (~100 ميجابايت + حِمل دائم على
  D-Bus). أُزيلت؛ الحاوية أصبحت أمينة: لا `systemctl` ولا `journalctl`, وبدلها
  مسار systemd الحقيقي على VPS.
- **OpenClaw** و**Node.js** و**Python 3.15**: إنتهى استخدامها، فأُزيلت.

## تدفّق الطلب

```
المتصفح → :3000 (nginx) → 127.0.0.1:6080 (websockify) → 127.0.0.1:5901 (Xtigervnc) → X11 :1 → XFCE
```

`/` سطح المكتب عند `PUBLIC_DESKTOP=1` (مع Basic Auth)، و`/status` صفحة الحالة،
و`/api/status` بيانات JSON، و`/healthz` فحص الصحة.

لماذا لا ينقطع: nginx يمرّر WebSocket بنفسه وبمهلة سبعة أيام،
و`Xtigervnc -noreset` يبقي الجلسة عند إعادة التحميل أو نوم الجهاز،
و`websockify --heartbeat 20` يبقي النفق دافئاً. لا سوكيت بايثون في المسار.

## systemd

داخل الحاوية لا توجد systemd حقيقية: supervisord هو PID 1 ولا يحاكي شيئاً.

**systemd الحقيقية** متوفرة عبر مسار VPS الأصلي:

```
vps/install.sh          # تثبيت على Ubuntu 24.04 نظيف
vps/systemd/*.service   # vnc, websockify, xfce, nginx, control
```

راجع `docs/DEPLOY-VPS.md`.

## القيود

- لا GPU: رسم برمجي (llvmpipe).
- الملفات تضيع عند إعادة نشر الحاوية.
- لا صوت.
- Basic Auth وحده ليس بديلاً عن TLS أمام أي نشر عام.

## الأمان

- `VNC_PW` إلزامية وقوية، ولا تُخزَّن في الصورة (يُحوَّل إلى hash apr1 عند
  الإقلاع).
- Xtigervnc يستمع على loopback فقط؛ nginx هو المدخل العام الوحيد.
- عند `PUBLIC_DESKTOP=1` يكون الرابط العام محمياً بـ Basic Auth؛ تعامله
  ككلمة سر وأوقف كلمة السر إذا تسرّب.
