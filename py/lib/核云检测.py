# 神殇

import time
import smtplib
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

# QQ邮箱
def send_qq_mail(content:str):
        
    # 要发送邮件的qq账号
    sender = "发送QQ号@qq.com" 
    # smtp授权码
    password = "发送QQ邮箱授权码" 
    # 收件人
    receiver = "收件人QQ" 
    
    # 创建
    msg = MIMEText(content, 'plain', 'utf-8')
    msg['From'] = sender
    msg['To'] = receiver
    msg['Subject'] = "核云补货啦"
    
    # 发送
    server = smtplib.SMTP_SSL("smtp.qq.com", 465)
    server.login(sender, password)
    server.sendmail(sender, [receiver], msg.as_string())
    server.quit()
    
    


# foxmail
def send_fox_mail(content:str):
    
    # 配置
    sender = "发送账号"
    password = "smtp"
    receiver = "接收账号"

    # 创建
    msg = MIMEText(content, 'plain',     'utf-8')
    msg['From'] = sender
    msg['To'] = receiver
    msg['Subject'] = "核云补货啦"
    
    #发送
    server = smtplib.SMTP_SSL("smtp.qq.com", 465)
    server.login(sender, password)
    server.sendmail(sender, [receiver], msg.as_string())
    server.quit()




def find_cart(count:int):
    # 哪个活动就替换哪个活动的链接
    url = 'https://www.heyunidc.cn/cart'
     
    total = 0
    while True:
        response = requests.get(url=url, timeout=10)

        #print(response)

        soup = BeautifulSoup(response.text,'html.parser')

        soup = soup.find_all('p',{'class':'stock-info'})

        aim = soup[count-1].get_text()

        #print(aim)
        print(f'[{total+1}]监听')
        if aim != '库存0':
            print('补货啦')
            # 如果你配置的是qq邮箱就启动第1个，如果你配置的是foxmail就启动第二个
            #send_qq_mail(f'核云补货啦{aim}')
            send_fox_mail(f'核云补货啦{aim}')
            break
        else:            
            print(aim+'\n')
            
        total += 1   
        # 每隔10秒监听一次        
        time.sleep(10)
            
# 当前页面，第几个板块就填几
find_cart(1)



