# KageAnime: free hosting guide

Yeh project Netlify (frontend) aur Render (Python backend) ke liye tayyar hai. Pehle Render backend deploy karein; phir Netlify mein uska URL set karein. Dono websites ka free plan aur limits waqt ke saath badal sakte hain.

## 1. GitHub par project upload karein
1. Ek GitHub repository banayein.
2. Is folder ki tamam files repository ke **root** mein upload karein: `app.py`, `index.html`, `watch.html`, `black-torch.webp`, `toon_archive.json`, `external_snapshot.json`, `requirements.txt`, `render.yaml`, `netlify.toml` aur `prepare_netlify.py`.
3. `toon_archive.json` cached listings ka snapshot hai. Backend usay baad mein dobara update karne ki koshish karega.

## 2. Render par Python backend
1. Render mein sign in karke apni repository connect karein aur **New → Blueprint** se `render.yaml` use karein. Agar Blueprint option istemal nahi kar rahe, naya Python Web Service banayein: build command `pip install -r requirements.txt`; start command `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 90`; health check path `/health`.
2. Free plan available ho to select karein. Deploy complete hone par apne service ka HTTPS URL copy karein, misal: `https://your-service.onrender.com` (yeh sirf example hai).
3. Backend ke URL ke baad `/health` khol kar check karein. `{"status":"ok"}` aana chahiye.

## 3. Netlify par frontend
1. Netlify mein **Add new project → Import an existing project** se wahi GitHub repository select karein.
2. Build settings `netlify.toml` se aa jayengi: build command `python3 prepare_netlify.py`; publish directory `public`.
3. Deploy se pehle Netlify ke environment variables mein `BACKEND_URL` naam ka variable banayein; value mein apna **asli Render HTTPS URL** likhein, kisi `/api` ya doosre path ke baghair.
4. Deploy karein. Jab backend URL badle to variable update karke Netlify par dobara deploy karein.
5. Website par search, anime card, alag watch page aur numbered episode buttons check karein.

## Automatic updates aur limitations
- DesiDubAnime ka catalog takriban 5-minute cache par refresh hota hai; open browser pages bhi periodically refresh hote hain. Website ko har update ke liye redeploy nahi karna padta.
- Aap offline ho sakte hain; lekin backend ka online hona zaroori hai. Render ki free service inactivity par sleep ho sakti hai: sleep mein background refresh nahi chalega; agli request par service wake hoke data refresh kar sakti hai, jis mein delay ho sakta hai.
- Source ki availability, access restrictions, site structure changes, third-party video players aur hosting limits ki wajah se updates aur playback guarantee nahi hain. Source apna structure badle to code update karna pad sakta hai.
- Yeh guide deployment prepare karti hai; actual hosting accounts mein aap ko steps complete karne honge.

## Real login, profile aur synced watch history (Supabase)

Is feature ke liye ek Supabase project zaroori hai. Jab tak configure nahi hota, account button setup-needed message dikhayega. Supabase ka free plan aur limits badal sakte hain.

1. https://supabase.com par account banayein, **New project** select karein, naam/password/region set karein aur project ready hone ka wait karein.
2. Project ke **SQL Editor** mein `supabase_setup.sql` ki poori contents copy karke **Run** karein. Isse profiles aur watch-history tables ke saath row-level security policies banengi. SQL ke errors hon to pehle unhein resolve karein.
3. Supabase **Project Settings → API / Connect** mein **Project URL** aur **publishable key** (ya purani `anon` public key) copy karein. **Service role, secret key ya database password kabhi website, browser ya kisi aur ko na dein.**
4. Render dashboard → `kageanime-backend` → **Environment** mein variables add karein:
   - `SUPABASE_URL` = project ka HTTPS URL.
   - `SUPABASE_PUBLISHABLE_KEY` = public publishable ya anon key.
   Save karke service ko redeploy/restart karein. Inki values GitHub repository mein mat likhein.
5. Supabase **Authentication → URL Configuration** mein **Site URL** ko apni actual website URL se set karein. Agar Netlify domain use karte hain, wahi set karein. Email signup confirmation Supabase settings ke mutabiq ho sakti hai.
6. Website khol kar **Sign in → Create account** se test account banayein, zaroori ho to email confirm karein, phir sign in karein. Profile mein anime-style avatar aur name choose karke save karein.
7. Ek anime ka watch page kholen; phir doosre device par usi account se sign in karke **Watch history** dekhein. History watch page/episode kholne par record hoti hai; video ki exact playback position track nahi hoti.

History aur profile Supabase mein rakhe jaate hain; doosre accounts ko access dene wali RLS policy na lagayein. Agar email confirmation ya tables setup na hon to signup/profile kaam nahi karega. The feature is not configured merely by uploading the code.
