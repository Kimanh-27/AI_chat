import mysql.connector

def get_db_connection():
    return mysql.connector.connect(
        host="btibidhyeviwrvesugp0-mysql.services.clever-cloud.com",
        user="u7qdi3nsyykv3syt",       
        password="0WtGTpj4JcW6YORPRs9j",       
        database="btibidhyeviwrvesugp0" 
    )