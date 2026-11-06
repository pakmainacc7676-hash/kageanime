# KageAnime: free hosting guide

Yeh project Netlify (frontend) aur Render (Python backend) ke liye tayyar hai. Pehle Render backend deploy karein; phir Netlify mein uska URL set karein. Dono websites ka free plan aur limits waqt ke saath badal sakte hain.

## 1. GitHub par project upload karein
1. Ek GitHub repository banayein.
2. Is folder ki tamam files repository ke **root** mein upload karein: `app.py`, `index.html`, `watch.html`, `black-torch.webp`, `toon_archive.json`, `requirements.txt`, `render.yaml`, `netlify.toml` aur `prepare_netlify.py`.
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
