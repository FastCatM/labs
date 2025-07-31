special = {'IV': 4, 'IX': 9, 'XL': 40, 'XC': 90, 'CD': 400, 'CM': 900}
roman={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
s=input()
sum_num=0
for k,v in special.items():
    count_key=s.count(k)
    if  count_key>0:
        s=s.replace(k,'-')
        sum_num+=count_key*v
for k,v in roman.items():
    count_key=s.count(k)
    if  count_key>0:
        s=s.replace(k,'-')
        sum_num+=count_key*v
print(sum_num ,s )