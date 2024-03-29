pipeline {
    agent none
    options {
        skipStagesAfterUnstable()
        buildDiscarder logRotator(numToKeepStr: '10')
    }
    parameters {
        booleanParam defaultValue: true, description: 'Publish to Test PyPI', name: 'PublishTestPyPI'
    }
    stages {
        stage('Build') {
            agent {
                docker {
                    label 'linux'
                    image 'python:3.9.7'
                }
            }
            steps {
                sh '''
                    git clean -fdx
                    python --version
                    pwd
                    df -h
                    ls -l
                '''
                sh 'python setup.py sdist bdist_wheel'
                stash(name: 'compiled-results', includes: 'dist/*.whl*')
            }
        }
        stage('Test') {
            agent {
                docker {
                    label 'linux'
                    image 'python:3.9.7'
                    args '-e HOME=/var/jenkins_home_tmp --tmpfs /var/jenkins_home_tmp:exec'
                }
            }
            steps {
                unstash(name: 'compiled-results')
                sh 'pip install --extra-index-url https://test.pypi.org/simple/ dist/tstriage-0.1.$BUILD_NUMBER-py3-none-any.whl'
            }
        }
        stage('Deploy') {
            when {
                expression {params.PublishTestPyPI == true}
            }
            agent {
                docker {
                    label 'linux'
                    image 'python:3.9.7'
                    args '-e HOME=/var/jenkins_home_tmp --tmpfs /var/jenkins_home_tmp:exec'
                }
            }
            steps {
                unstash(name: 'compiled-results')
                archiveArtifacts artifacts: 'dist/*.whl', fingerprint: true
                sh 'pip install twine'
                withCredentials([usernamePassword(credentialsId: '65ddf05a-75ed-43cd-ab7e-5ac1e6af2526', usernameVariable: 'USERNAME', passwordVariable: 'PASSWORD')]) {
                    sh 'python -m twine upload -r testpypi dist/* -u $USERNAME -p $PASSWORD'
                }
            }
        }
    }
    post {
        aborted {
            echo 'aborted'
            emailext subject: "ABORTED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" aborted\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        failure {
            echo 'failure'
            emailext subject: "FAILED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" failed\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        success {
            echo 'success'
            emailext subject: "SUCCEEDED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]'",
                body: """Job "${env.JOB_NAME} [${env.BUILD_NUMBER}]" succeeded\nCheck console output at ${env.BUILD_URL}\n""",
                recipientProviders: [[$class: 'DevelopersRecipientProvider'], [$class: 'RequesterRecipientProvider']]
        }
        cleanup {
            echo 'Cleaning up ...'
        }
    }
}
