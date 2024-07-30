Tricks to run the Redbox in MAC (e.g. the one we use in Civil Servants Expo)
Open Visual Code

Run the docker:
Gmake Run 
or docker compose-up 

Remember to connect to our own LLM runin the AI-tools with the command in VS code/terminal:
Example: kubectl port-forward zephyr-6dfb6d878-lw6w9 6000:6000
"zephyr-6dfb6d878-lw6w9" is the microservice of zerphy that are running, 
to find the current name of that microservice
"kubectl get pods" (this assume you have access to the AWS clusters, so you would need access to the AWS account)
And look for the NAME begins with zephyr… and use it for the port-forward command


To use Redbox Copilot either
Open the web browser and enter this website : http://localhost:8090/documents/
For the civil demo MAC, It is also stored in the Favourite in Safari


If it ask you to sign in..then
enter a email address e.g. “herbert@6point6.com” (which is used in the MAC demo)
Note that RedBox-copilot are supposed to send you the magic link to sign in, 
but that parts of the code has bugs, so it doesnt work.
In order to get the magic link:
Open docker app, Click on the Django-app 8080:8090 to view the logs of Django-app in docker,
Search for magic link by searcing for "send link"
You should see something like this:
send link http://localhost:8090/magic_link/f0a9e8f1-f80c-45cd-8406-0c75bb81bc1e/
(Note the part after magic_link/ is randomly generated)

Click on the link and it should get into the account.
Note you can sign out and sing in with different account,
But the document will be empty if you use a new accounr, so
you would need to upload and embedded documents again,

Note:elasticsearch performance is poor in Mac.
